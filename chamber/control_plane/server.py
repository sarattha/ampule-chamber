"""FastAPI adapter and server-rendered local control plane."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import secrets
import shutil
import threading
import uuid
import webbrowser
from dataclasses import asdict
from pathlib import Path
from typing import Annotated, Any, cast
from urllib.parse import urlencode

import yaml
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
from starlette.responses import StreamingResponse

from chamber.application.service import ChamberApplication
from chamber.control_plane.discovery import (
    DiscoveryError,
    DiscoverySettings,
    KubernetesDiscovery,
)
from chamber.control_plane.goals import goal_catalog, propose_goal
from chamber.control_plane.jobs import TERMINAL_JOB_STATES, AssessmentJobManager
from chamber.control_plane.scenarios import (
    ScenarioCatalog,
    ScenarioCatalogError,
    compatibility_warnings,
    normalize_document,
    parse_scenario_document,
)
from chamber.control_plane.security import (
    SESSION_COOKIE,
    load_admin_auth,
    safe_next_path,
)
from chamber.load import validate_relayna_journey
from chamber.runs import registered_evidence

PACKAGE_DIR = Path(__file__).resolve().parent
TEMPLATE_DIR = PACKAGE_DIR / "templates"
STATIC_DIR = PACKAGE_DIR / "static"
CSRF_COOKIE = "ampule_csrf"
MAX_UI_UPLOAD_BYTES = 128 * 1024 * 1024
MAX_UI_TOTAL_UPLOAD_BYTES = 256 * 1024 * 1024


class InspectRequest(BaseModel):
    repo: str


class PlanRequest(BaseModel):
    config: dict[str, Any]


class RunStartRequest(BaseModel):
    config_path: str
    mode: str = Field(pattern="^(local|kubernetes)$")
    context: str | None = None
    prometheus_url: str | None = None


class CompareRequest(BaseModel):
    baseline_run_id: str
    candidate_run_id: str


class RerunRequest(BaseModel):
    kubernetes_context: str = ""
    prometheus_url: str = ""


class ScenarioValidateRequest(BaseModel):
    content: str
    service_name: str = ""


class ScenarioSaveRequest(BaseModel):
    document: dict[str, Any]
    replace: bool = False


class GoalProposalRequest(BaseModel):
    goal: str
    service_name: str = ""
    workload_name: str = ""
    service_port: int | None = Field(default=None, ge=1, le=65535)
    request_path: str = ""
    repository_available: bool = False
    attach_mode: bool = False
    discovery_complete: bool = False
    dependency_names: tuple[str, ...] = ()
    telemetry_available: tuple[str, ...] = ()


def create_app(
    workspace: Path = Path(".chamber"),
    *,
    admin_token: str | None = None,
    discovery: KubernetesDiscovery | None = None,
) -> FastAPI:
    """Create the local control-plane application."""

    auth = load_admin_auth(admin_token)
    kubernetes_discovery = discovery or KubernetesDiscovery(DiscoverySettings.from_environment())
    application = ChamberApplication(workspace)
    application.initialize()
    jobs = AssessmentJobManager(workspace)
    scenarios = ScenarioCatalog(workspace, PACKAGE_DIR / "bundled_scenarios")
    multipart_path_secret = secrets.token_bytes(32)
    multipart_upload_root = workspace.resolve() / "uploads"
    templates = Jinja2Templates(directory=TEMPLATE_DIR)
    app = FastAPI(title="Ampule Chamber Control Plane", version="1")
    app.state.chamber = application
    app.state.jobs = jobs
    app.state.templates = templates
    app.state.workspace = workspace
    app.state.auth = auth
    app.state.discovery = kubernetes_discovery
    app.state.scenarios = scenarios
    cast(dict[str, Any], templates.env.globals)["auth_enabled"] = auth.enabled
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.middleware("http")
    async def security_headers(request: Request, call_next: Any) -> Response:
        token = request.cookies.get(CSRF_COOKIE) or secrets.token_urlsafe(32)
        request.state.csrf_token = token
        request.state.authenticated = auth.authenticates_request(request)
        if not request.state.authenticated and not _public_path(request.url.path):
            if request.url.path.startswith("/api/"):
                response = JSONResponse(
                    {"detail": "authentication required"},
                    status_code=401,
                    headers={"WWW-Authenticate": "Bearer"},
                )
            else:
                next_path = request.url.path
                if request.url.query:
                    next_path += f"?{request.url.query}"
                response = RedirectResponse(
                    f"/login?{urlencode({'next': next_path})}", status_code=303
                )
        else:
            response = await call_next(request)
        if request.cookies.get(CSRF_COOKIE) is None:
            response.set_cookie(
                CSRF_COOKIE,
                token,
                httponly=False,
                samesite="strict",
                secure=auth.secure_cookies,
            )
        return _apply_security_headers(response)

    @app.get("/healthz", include_in_schema=False)
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/readyz", include_in_schema=False)
    async def ready() -> dict[str, str]:
        return {"status": "ready"}

    @app.get("/login", response_class=HTMLResponse, include_in_schema=False)
    async def login_page(request: Request, next: str = "/runs") -> Response:
        if not auth.enabled or request.state.authenticated:
            return RedirectResponse(safe_next_path(next), status_code=303)
        return templates.TemplateResponse(
            request=request,
            name="login.html",
            context={
                "csrf_token": request.state.csrf_token,
                "next_path": safe_next_path(next),
                "error": None,
            },
        )

    @app.post("/login", response_class=HTMLResponse, include_in_schema=False)
    async def login(
        request: Request,
        csrf: Annotated[str, Form(alias="_csrf")],
        admin_token_value: Annotated[str, Form(alias="admin_token")],
        next: Annotated[str, Form()] = "/runs",
    ) -> Response:
        _check_csrf(request, csrf)
        next_path = safe_next_path(next)
        if not auth.authenticates_token(admin_token_value):
            return templates.TemplateResponse(
                request=request,
                name="login.html",
                status_code=401,
                context={
                    "csrf_token": request.state.csrf_token,
                    "next_path": next_path,
                    "error": "The admin token is not valid.",
                },
            )
        response = RedirectResponse(next_path, status_code=303)
        response.set_cookie(
            SESSION_COOKIE,
            auth.session_value or "",
            httponly=True,
            samesite="strict",
            secure=auth.secure_cookies,
        )
        return response

    @app.post("/logout", include_in_schema=False)
    async def logout(
        request: Request,
        csrf: Annotated[str, Form(alias="_csrf")],
    ) -> Response:
        _check_csrf(request, csrf)
        response = RedirectResponse("/login", status_code=303)
        response.delete_cookie(SESSION_COOKIE)
        return response

    @app.get("/", include_in_schema=False)
    async def home() -> RedirectResponse:
        return RedirectResponse("/runs", status_code=303)

    @app.get("/runs", response_class=HTMLResponse, include_in_schema=False)
    async def runs_page(request: Request) -> Response:
        runs = application.list_runs(limit=200)
        return templates.TemplateResponse(
            request=request,
            name="runs.html",
            context={"runs": runs, "active_nav": "runs", "csrf_token": request.state.csrf_token},
        )

    @app.get("/new", response_class=HTMLResponse, include_in_schema=False)
    async def new_assessment_page(request: Request) -> Response:
        return templates.TemplateResponse(
            request=request,
            name="new.html",
            context={
                "active_nav": "new",
                "csrf_token": request.state.csrf_token,
                "capabilities": _capabilities(kubernetes_discovery.settings),
                "reliability_goals": goal_catalog(),
            },
        )

    @app.post("/ui/plan", include_in_schema=False)
    async def plan_from_form(
        request: Request,
        csrf: Annotated[str, Form(alias="_csrf")],
        repo: Annotated[str, Form()] = "",
        service_name: Annotated[str, Form()] = "",
        workload_name: Annotated[str, Form()] = "",
        workload_kind: Annotated[str, Form()] = "Deployment",
        execution_mode: Annotated[str, Form()] = "local",
        runtime_mode: Annotated[str, Form()] = "deploy",
        kubernetes_context: Annotated[str, Form()] = "",
        namespace: Annotated[str, Form()] = "",
        service_port: Annotated[int, Form()] = 8080,
        journeys_json: Annotated[str, Form()] = "",
        journey_files: Annotated[list[UploadFile] | None, File()] = None,
        journey_type: Annotated[str, Form()] = "http",
        traffic_path: Annotated[str, Form()] = "/health",
        traffic_profile: Annotated[str, Form()] = "baseline",
        request_body: Annotated[str, Form()] = "",
        events_path: Annotated[str, Form()] = "/events/{task_id}",
        task_id_path: Annotated[str, Form()] = "task_id",
        relayna_timeout_seconds: Annotated[int, Form()] = 300,
        fault_type: Annotated[str, Form()] = "none",
        prometheus_url: Annotated[str, Form()] = "",
        agents_mode: Annotated[str, Form()] = "offline",
        agents_exclude_json: Annotated[str, Form()] = "[]",
        scenario_id: Annotated[str, Form()] = "",
        scenario_name: Annotated[str, Form()] = "",
        scenario_description: Annotated[str, Form()] = "",
        scenario_tags: Annotated[str, Form()] = "",
        scenario_source: Annotated[str, Form()] = "custom",
        scenario_revision: Annotated[str, Form()] = "",
        required_signals_json: Annotated[str, Form()] = "[]",
        save_scenario: Annotated[str, Form()] = "none",
        replace_scenario: Annotated[str, Form()] = "",
    ) -> Response:
        _check_csrf(request, csrf)
        upload_dir: Path | None = None
        try:
            journeys_json, upload_dir = await _persist_journey_files(
                workspace,
                journeys_json,
                journey_files or [],
                path_secret=multipart_path_secret,
            )
            run_dir = _plan_from_values(
                application,
                workspace=workspace,
                repo=Path(repo) if repo.strip() else None,
                service_name=service_name,
                workload_name=workload_name,
                workload_kind=workload_kind,
                execution_mode=execution_mode,
                runtime_mode=runtime_mode,
                kubernetes_context=kubernetes_context,
                namespace=namespace,
                service_port=service_port,
                journeys_json=journeys_json,
                journey_type=journey_type,
                traffic_path=traffic_path,
                traffic_profile=traffic_profile,
                request_body=request_body,
                events_path=events_path,
                task_id_path=task_id_path,
                relayna_timeout_seconds=relayna_timeout_seconds,
                fault_type=fault_type,
                prometheus_url=prometheus_url,
                agents_mode=agents_mode,
                agents_exclude_json=agents_exclude_json,
                scenario_id=scenario_id,
                scenario_name=scenario_name,
                scenario_description=scenario_description,
                scenario_tags=scenario_tags,
                scenario_source=scenario_source,
                scenario_revision=scenario_revision,
                required_signals_json=required_signals_json,
                save_scenario=save_scenario,
                replace_scenario=replace_scenario,
                catalog=scenarios,
            )
        except (OSError, ValueError, RuntimeError) as exc:
            if upload_dir is not None:
                shutil.rmtree(upload_dir, ignore_errors=True)
            return templates.TemplateResponse(
                request=request,
                name="new.html",
                status_code=400,
                context={
                    "active_nav": "new",
                    "csrf_token": request.state.csrf_token,
                    "capabilities": _capabilities(kubernetes_discovery.settings),
                    "reliability_goals": goal_catalog(),
                    "error": str(exc),
                },
            )
        return RedirectResponse(f"/runs/{run_dir.name}?tab=configuration", status_code=303)

    @app.get("/runs/{run_id}", response_class=HTMLResponse, include_in_schema=False)
    async def run_page(request: Request, run_id: str, tab: str = "overview") -> Response:
        try:
            run = application.get_run(run_id)
        except (FileNotFoundError, ValueError):
            raise HTTPException(status_code=404, detail="run not found") from None
        allowed_tabs = {"overview", "timeline", "findings", "evidence", "configuration", "agents"}
        selected_tab = tab if tab in allowed_tabs else "overview"
        return templates.TemplateResponse(
            request=request,
            name="run.html",
            context={
                "run_data": run,
                "run_id": run_id,
                "selected_tab": selected_tab,
                "active_nav": "runs",
                "csrf_token": request.state.csrf_token,
            },
        )

    @app.post("/ui/runs/{run_id}/start", include_in_schema=False)
    async def start_run_page(
        request: Request,
        run_id: str,
        csrf: Annotated[str, Form(alias="_csrf")],
    ) -> Response:
        _check_csrf(request, csrf)
        try:
            planned = application.get_run(run_id)
            run_dir = application.run_path(run_id)
        except (FileNotFoundError, ValueError):
            raise HTTPException(status_code=404, detail="run not found") from None
        config = _mapping(planned.get("config"))
        runtime = _mapping(config.get("runtime"))
        mode = "kubernetes" if runtime.get("provider") == "kubernetes" else "local"
        job = jobs.start(
            run_dir / "chamber.yaml",
            mode=mode,
            context=(
                str(runtime.get("kubernetesContext")) if runtime.get("kubernetesContext") else None
            ),
            prometheus_url=(
                str(runtime.get("prometheusUrl")) if runtime.get("prometheusUrl") else None
            ),
        )
        return RedirectResponse(f"/jobs/{job['job_id']}", status_code=303)

    @app.post("/ui/runs/{run_id}/fix-and-rerun", include_in_schema=False)
    async def fix_and_rerun_page(
        request: Request,
        run_id: str,
        csrf: Annotated[str, Form(alias="_csrf")],
        kubernetes_context: Annotated[str, Form()] = "",
        prometheus_url: Annotated[str, Form()] = "",
    ) -> Response:
        _check_csrf(request, csrf)
        try:
            planned = application.plan_rerun(
                run_id,
                kubernetes_context=kubernetes_context,
                prometheus_url=prometheus_url,
            )
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail="run not found") from None
        except (OSError, ValueError, RuntimeError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return RedirectResponse(f"/runs/{planned.name}?tab=configuration", status_code=303)

    @app.get("/jobs/{job_id}", response_class=HTMLResponse, include_in_schema=False)
    async def job_page(request: Request, job_id: str) -> Response:
        try:
            job = jobs.get(job_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="job not found") from None
        return templates.TemplateResponse(
            request=request,
            name="job.html",
            context={
                "job": job,
                "active_nav": "runs",
                "csrf_token": request.state.csrf_token,
            },
        )

    @app.post("/ui/jobs/{job_id}/cancel", include_in_schema=False)
    async def cancel_job_page(
        request: Request,
        job_id: str,
        csrf: Annotated[str, Form(alias="_csrf")],
    ) -> Response:
        _check_csrf(request, csrf)
        try:
            jobs.cancel(job_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="job not found") from None
        return RedirectResponse(f"/jobs/{job_id}", status_code=303)

    @app.get("/compare", response_class=HTMLResponse, include_in_schema=False)
    async def compare_page(
        request: Request,
        baseline: str = "",
        candidate: str = "",
    ) -> Response:
        comparison = None
        error = None
        if baseline and candidate:
            try:
                comparison = asdict(application.compare(baseline, candidate))
            except (FileNotFoundError, ValueError) as exc:
                error = str(exc)
        return templates.TemplateResponse(
            request=request,
            name="compare.html",
            context={
                "runs": application.list_runs(limit=200),
                "comparison": comparison,
                "error": error,
                "baseline": baseline,
                "candidate": candidate,
                "active_nav": "runs",
                "csrf_token": request.state.csrf_token,
            },
        )

    @app.get("/api/v1/capabilities")
    async def capabilities_api() -> dict[str, Any]:
        return _capabilities(kubernetes_discovery.settings)

    @app.get("/api/v1/scenarios")
    async def scenarios_api() -> dict[str, Any]:
        return {"scenarios": scenarios.list(_ui_journeys)}

    @app.get("/api/v1/scenarios/{source}/{scenario_id}")
    async def scenario_api(source: str, scenario_id: str, service_name: str = "") -> dict[str, Any]:
        try:
            normalized = scenarios.read(source, scenario_id, _ui_journeys)
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail="scenario not found") from None
        except (ScenarioCatalogError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        normalized["warnings"] = compatibility_warnings(normalized, service_name=service_name)
        _authorize_multipart_paths(normalized, multipart_path_secret, multipart_upload_root)
        return normalized

    @app.post("/api/v1/scenarios/validate")
    async def validate_scenario_api(
        request: Request, payload: ScenarioValidateRequest
    ) -> dict[str, Any]:
        _check_csrf(request, request.headers.get("X-CSRF-Token"))
        try:
            normalized = normalize_document(
                parse_scenario_document(payload.content),
                source="imported",
                validate_journeys=_ui_journeys,
            )
        except (ScenarioCatalogError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        normalized["warnings"] = compatibility_warnings(
            normalized, service_name=payload.service_name
        )
        _authorize_multipart_paths(normalized, multipart_path_secret, multipart_upload_root)
        return normalized

    @app.post("/api/v1/scenarios", status_code=201)
    async def create_scenario_api(request: Request, payload: ScenarioSaveRequest) -> dict[str, Any]:
        _check_csrf(request, request.headers.get("X-CSRF-Token"))
        try:
            return scenarios.save(
                payload.document,
                replace=payload.replace,
                validate_journeys=_ui_journeys,
            )
        except FileExistsError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except (ScenarioCatalogError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/v1/scenarios/propose")
    async def propose_scenario_api(
        request: Request, payload: GoalProposalRequest
    ) -> dict[str, Any]:
        _check_csrf(request, request.headers.get("X-CSRF-Token"))
        try:
            return propose_goal(
                payload.goal,
                service_name=payload.service_name,
                workload_name=payload.workload_name,
                service_port=payload.service_port,
                request_path=payload.request_path,
                repository_available=payload.repository_available,
                attach_mode=payload.attach_mode,
                discovery_complete=payload.discovery_complete,
                dependency_names=payload.dependency_names,
                telemetry_available=payload.telemetry_available,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/v1/kubernetes/discovery")
    async def kubernetes_discovery_api(context: str, namespace: str) -> dict[str, Any]:
        try:
            return kubernetes_discovery.discover(context=context, namespace=namespace)
        except (DiscoveryError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/v1/inspect")
    async def inspect_api(request: Request, payload: InspectRequest) -> dict[str, Any]:
        _check_csrf(request, request.headers.get("X-CSRF-Token"))
        repo = Path(payload.repo)
        if not repo.is_dir():
            raise HTTPException(status_code=400, detail="repository path does not exist")
        return application.inspect_repository(repo)

    @app.post("/api/v1/plans")
    async def plans_api(request: Request, payload: PlanRequest) -> dict[str, str]:
        _check_csrf(request, request.headers.get("X-CSRF-Token"))
        config_path = _write_draft(workspace, payload.config)
        try:
            run_dir = application.plan(config_path)
        except (OSError, ValueError, RuntimeError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"run_id": run_dir.name, "run_dir": str(run_dir)}

    @app.get("/api/v1/runs")
    async def list_runs_api(limit: int = 100) -> dict[str, Any]:
        return {"runs": application.list_runs(limit=limit)}

    @app.post("/api/v1/runs", status_code=202)
    async def start_run_api(request: Request, payload: RunStartRequest) -> dict[str, Any]:
        _check_csrf(request, request.headers.get("X-CSRF-Token"))
        config_path = _safe_config_path(workspace, payload.config_path)
        return jobs.start(
            config_path,
            mode=payload.mode,
            context=payload.context,
            prometheus_url=payload.prometheus_url,
        )

    @app.get("/api/v1/runs/{run_id}")
    async def run_api(run_id: str) -> dict[str, Any]:
        try:
            return application.get_run(run_id)
        except (FileNotFoundError, ValueError):
            raise HTTPException(status_code=404, detail="run not found") from None

    @app.post("/api/v1/runs/{run_id}/rerun", status_code=201)
    async def rerun_api(
        request: Request,
        run_id: str,
        payload: RerunRequest,
    ) -> dict[str, str]:
        _check_csrf(request, request.headers.get("X-CSRF-Token"))
        try:
            planned = application.plan_rerun(
                run_id,
                kubernetes_context=payload.kubernetes_context,
                prometheus_url=payload.prometheus_url,
            )
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail="run not found") from None
        except (OSError, ValueError, RuntimeError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"run_id": planned.name, "run_dir": str(planned)}

    @app.get("/api/v1/runs/{run_id}/events")
    async def run_events_api(run_id: str) -> StreamingResponse:
        try:
            run_dir = application.run_path(run_id)
        except (FileNotFoundError, ValueError):
            raise HTTPException(status_code=404, detail="run not found") from None
        return StreamingResponse(_run_event_stream(run_dir), media_type="text/event-stream")

    @app.get("/api/v1/jobs/{job_id}")
    async def job_api(job_id: str) -> dict[str, Any]:
        try:
            return jobs.get(job_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="job not found") from None

    @app.get("/api/v1/jobs/{job_id}/events")
    async def job_events_api(job_id: str) -> StreamingResponse:
        try:
            jobs.get(job_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="job not found") from None
        return StreamingResponse(_job_event_stream(jobs, job_id), media_type="text/event-stream")

    @app.post("/api/v1/jobs/{job_id}/cancel")
    async def cancel_job_api(request: Request, job_id: str) -> dict[str, Any]:
        _check_csrf(request, request.headers.get("X-CSRF-Token"))
        try:
            return jobs.cancel(job_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="job not found") from None

    @app.get("/api/v1/runs/{run_id}/evidence/{evidence_id}")
    async def evidence_api(run_id: str, evidence_id: str) -> FileResponse:
        try:
            run_dir = application.run_path(run_id)
        except (FileNotFoundError, ValueError):
            raise HTTPException(status_code=404, detail="run not found") from None
        item = next(
            (
                value
                for value in registered_evidence(run_dir)
                if value.get("evidence_id") == evidence_id
            ),
            None,
        )
        if item is None:
            raise HTTPException(status_code=404, detail="evidence not found")
        relative = item.get("relative_path")
        if not isinstance(relative, str):
            raise HTTPException(status_code=404, detail="evidence not found")
        return FileResponse(run_dir / relative, filename=Path(relative).name)

    @app.get("/api/v1/runs/{run_id}/report")
    async def report_api(request: Request, run_id: str, format: str = "markdown") -> Response:
        try:
            run_dir = application.run_path(run_id)
            report_path = application.report(run_dir)
            run = application.get_run(run_id)
        except (FileNotFoundError, ValueError):
            raise HTTPException(status_code=404, detail="run not found") from None
        if format == "json":
            return JSONResponse({"result": run["result"], "findings": run["findings"]})
        if format == "html":
            return templates.TemplateResponse(
                request=request,
                name="report.html",
                context={
                    "run_data": run,
                    "run_id": run_id,
                    "active_nav": "runs",
                    "csrf_token": request.state.csrf_token,
                },
            )
        return Response(report_path.read_text(encoding="utf-8"), media_type="text/markdown")

    @app.post("/api/v1/compare")
    async def compare_api(request: Request, payload: CompareRequest) -> dict[str, Any]:
        _check_csrf(request, request.headers.get("X-CSRF-Token"))
        try:
            return asdict(application.compare(payload.baseline_run_id, payload.candidate_run_id))
        except (FileNotFoundError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    return app


def run_server(
    *,
    host: str,
    port: int,
    workspace: Path,
    open_browser: bool,
    allow_remote: bool = False,
) -> int:
    """Run the local control plane until interrupted."""

    if host not in {"127.0.0.1", "localhost", "::1"} and not allow_remote:
        raise ValueError("remote binding requires --allow-remote")
    auth = load_admin_auth()
    if host not in {"127.0.0.1", "localhost", "::1"} and not auth.enabled:
        raise ValueError("remote binding requires AMPULE_CHAMBER_ADMIN_TOKEN")
    import uvicorn

    url = f"http://{host}:{port}/"
    if open_browser:
        threading.Timer(0.8, webbrowser.open, args=(url,)).start()
    uvicorn.run(create_app(workspace), host=host, port=port, log_level="info")
    return 0


def _plan_from_values(
    application: ChamberApplication,
    *,
    workspace: Path,
    repo: Path | None,
    service_name: str,
    workload_name: str,
    workload_kind: str,
    execution_mode: str,
    runtime_mode: str,
    kubernetes_context: str,
    namespace: str,
    service_port: int,
    journeys_json: str,
    journey_type: str,
    traffic_path: str,
    traffic_profile: str,
    request_body: str,
    events_path: str,
    task_id_path: str,
    relayna_timeout_seconds: int,
    fault_type: str,
    prometheus_url: str,
    agents_mode: str,
    agents_exclude_json: str = "[]",
    scenario_id: str = "",
    scenario_name: str = "",
    scenario_description: str = "",
    scenario_tags: str = "",
    scenario_source: str = "custom",
    scenario_revision: str = "",
    required_signals_json: str = "[]",
    save_scenario: str = "none",
    replace_scenario: str = "",
    catalog: ScenarioCatalog | None = None,
) -> Path:
    config: dict[str, Any]
    if repo is not None and not repo.is_dir():
        raise ValueError(f"repository path does not exist: {repo}")
    if repo is None:
        if execution_mode != "kubernetes" or runtime_mode != "attach":
            raise ValueError("repository path is required for local and deploy assessments")
        if not service_name.strip():
            raise ValueError("service name is required when attaching without a repository")
        if not workload_name.strip():
            raise ValueError("workload name is required when attaching without a repository")
        if workload_kind not in {"Deployment", "StatefulSet"}:
            raise ValueError("workload kind must be Deployment or StatefulSet")
        name = service_name.strip()
        config = {
            "apiVersion": "chamber.ampule.dev/v1alpha1",
            "kind": "ChamberConfig",
            "service": {
                "name": name,
                "repo": f"kubernetes://{kubernetes_context}/{namespace}/{name}",
            },
            "deployment": {
                "manifests": [],
                "images": {},
                "workloads": [
                    {
                        "name": workload_name.strip(),
                        "role": "target",
                        "kind": workload_kind,
                    }
                ],
            },
            "traffic": {"entrypoint": name, "journeys": []},
            "dependencies": {"internal": [], "external": []},
            "runtime": {"requiredEnv": [], "secretEnv": [], "config": {}},
            "agents": {"mode": agents_mode},
            "assumptions": (
                "Attached directly to an existing Kubernetes workload without "
                "repository inspection.",
            ),
        }
    else:
        config = application.inspect_repository(repo)
    service = cast(dict[str, Any], config["service"])
    if service_name.strip():
        service["name"] = service_name.strip()
    name = str(service["name"])
    traffic = cast(dict[str, Any], config["traffic"])
    traffic["entrypoint"] = name
    if journeys_json.strip():
        traffic["journeys"] = _ui_journeys(journeys_json, workspace=workspace)
    elif journey_type == "relayna":
        try:
            body = json.loads(request_body)
        except json.JSONDecodeError as exc:
            raise ValueError("Relayna request body must be valid JSON") from exc
        if not isinstance(body, dict) or not body:
            raise ValueError("Relayna request body must be a non-empty JSON object")
        if "{task_id}" not in events_path:
            raise ValueError("Relayna events path must contain {task_id}")
        if relayna_timeout_seconds <= 0:
            raise ValueError("Relayna timeout must be positive")
        relayna_profiles = {
            "smoke": (1, 1),
            "baseline": (4, 2),
            "stress": (10, 4),
        }
        iterations, vus = relayna_profiles.get(traffic_profile, relayna_profiles["smoke"])
        traffic["journeys"] = [
            {
                "name": "relayna-task-lifecycle",
                "adapter": "relayna",
                "method": "POST",
                "path": traffic_path or "/translations",
                "expectedStatus": 202,
                "body": body,
                "iterations": iterations,
                "vus": vus,
                "relayna": {
                    "taskIdPath": task_id_path or "task_id",
                    "eventsPath": events_path,
                    "terminalStatuses": ["completed", "failed"],
                    "successStatuses": ["completed"],
                    "timeoutSeconds": relayna_timeout_seconds,
                },
            }
        ]
    elif journey_type == "http":
        profiles = {
            "smoke": (("15s", 1), ("5s", 0)),
            "baseline": (("30s", 4), ("30s", 0)),
            "stress": (("30s", 10), ("60s", 25), ("30s", 0)),
        }
        stages = profiles.get(traffic_profile, profiles["baseline"])
        traffic["journeys"] = [
            {
                "name": "baseline-health",
                "method": "GET",
                "path": traffic_path or "/health",
                "expectedStatus": 200,
                "stages": [
                    {"duration": duration, "targetVus": target} for duration, target in stages
                ],
            }
        ]
    else:
        raise ValueError("journey type must be http or relayna")
    try:
        agent_exclusions = json.loads(agents_exclude_json or "[]")
    except json.JSONDecodeError as exc:
        raise ValueError("Agent exclusions must be valid JSON") from exc
    if not isinstance(agent_exclusions, list) or not all(
        isinstance(value, str) and value.strip() for value in agent_exclusions
    ):
        raise ValueError("Agent exclusions must be a JSON array of non-empty strings")
    config["agents"] = {"mode": agents_mode}
    if agent_exclusions:
        config["agents"]["exclude"] = agent_exclusions
    runtime = cast(dict[str, Any], config["runtime"])
    if execution_mode == "kubernetes":
        runtime.update(
            {
                "provider": "kubernetes",
                "mode": runtime_mode,
                "kubernetesContext": kubernetes_context,
                "cleanup": runtime_mode != "attach",
                "trafficAccess": {
                    "mode": "port-forward",
                    "service": name,
                    "servicePort": service_port,
                },
            }
        )
        if prometheus_url:
            runtime["prometheusUrl"] = prometheus_url
        deployment = cast(dict[str, Any], config["deployment"])
        if runtime_mode == "attach":
            runtime["namespace"] = namespace
            deployment["manifests"] = []
            deployment["services"] = [{"name": name, "port": service_port}]
            if fault_type == "pod_kill":
                runtime["faults"] = [{"type": "pod_kill"}]
            elif fault_type == "deployment_scale":
                runtime["faults"] = [{"type": "deployment_scale", "replicas": 0}]
            else:
                runtime["faults"] = []
        else:
            runtime["namespaceBase"] = namespace or f"chamber-{name}"
    else:
        runtime.update({"provider": "local", "mode": "deploy"})
        for key in (
            "kubernetesContext",
            "namespace",
            "namespaceBase",
            "cleanup",
            "trafficAccess",
            "prometheusUrl",
        ):
            runtime.pop(key, None)
    selected_scenario_id = scenario_id.strip() or f"{name}-assessment"
    tags = [value.strip() for value in scenario_tags.split(",") if value.strip()]
    try:
        signals = json.loads(required_signals_json or "[]")
    except json.JSONDecodeError as exc:
        raise ValueError("Required signals must be valid JSON") from exc
    if not isinstance(signals, list) or not all(
        isinstance(value, str) and value.strip() for value in signals
    ):
        raise ValueError("Required signals must be a JSON array of non-empty strings")
    origin_source = (
        scenario_source
        if scenario_source in {"custom", "bundled", "user", "imported"}
        else "custom"
    )
    matching_user_scenario = (
        _matching_user_scenario(
            catalog,
            scenario_id=selected_scenario_id,
            revision=scenario_revision.strip(),
            name=scenario_name.strip() or selected_scenario_id,
            description=scenario_description.strip(),
            tags=tags,
            target_service=name,
            target_service_port=service_port,
            journeys=cast(list[dict[str, Any]], traffic["journeys"]),
            required_signals=signals,
            agent_mode=agents_mode,
            agent_exclusions=agent_exclusions,
            fault_type=fault_type,
        )
        if origin_source == "user"
        else None
    )
    if origin_source == "custom":
        resolved_source = "custom"
    elif matching_user_scenario is not None:
        resolved_source = "user"
    else:
        resolved_source = "derived"
    config["scenarioId"] = selected_scenario_id
    scenario_metadata: dict[str, Any] = {
        "id": selected_scenario_id,
        "name": scenario_name.strip() or selected_scenario_id,
        "description": scenario_description.strip(),
        "tags": tags,
        "source": resolved_source,
        "revision": "draft",
        "requiredSignals": signals,
    }
    if origin_source != resolved_source:
        scenario_metadata["origin"] = {
            "source": origin_source,
            "revision": scenario_revision.strip() or "unrecorded",
        }
    elif matching_user_scenario is not None and isinstance(
        matching_user_scenario.get("origin"), dict
    ):
        scenario_metadata["origin"] = dict(matching_user_scenario["origin"])
    config["scenario"] = scenario_metadata
    normalized = normalize_document(config, source="custom", validate_journeys=_ui_journeys)
    if matching_user_scenario is not None and normalized["revision"] != scenario_revision.strip():
        scenario_metadata["source"] = "derived"
        scenario_metadata["origin"] = {
            "source": "user",
            "revision": scenario_revision.strip(),
        }
        normalized = normalize_document(config, source="custom", validate_journeys=_ui_journeys)
    config["scenario"]["revision"] = normalized["revision"]
    if save_scenario not in {"none", "new", "replace"}:
        raise ValueError("Save scenario mode must be none, new, or replace")
    if save_scenario != "none":
        if catalog is None:
            raise ValueError("Scenario catalog is unavailable")
        if save_scenario == "replace" and replace_scenario != "confirmed":
            raise ValueError("Replacing a saved scenario requires explicit confirmation")
        prepared = catalog.prepare_save(
            config,
            replace=save_scenario == "replace",
            validate_journeys=_ui_journeys,
        )
        config["scenario"].update({"source": "user", "revision": prepared["revision"]})
    config_path = _write_draft(workspace, config)
    try:
        run_dir = application.plan(config_path)
    except Exception:
        config_path.unlink(missing_ok=True)
        raise
    if save_scenario != "none":
        assert catalog is not None
        try:
            catalog.save(
                config,
                replace=save_scenario == "replace",
                validate_journeys=_ui_journeys,
            )
        except Exception:
            shutil.rmtree(run_dir, ignore_errors=True)
            config_path.unlink(missing_ok=True)
            raise
    return run_dir


def _matching_user_scenario(
    catalog: ScenarioCatalog | None,
    *,
    scenario_id: str,
    revision: str,
    name: str,
    description: str,
    tags: list[str],
    target_service: str,
    target_service_port: int,
    journeys: list[dict[str, Any]],
    required_signals: list[str],
    agent_mode: str,
    agent_exclusions: list[str],
    fault_type: str,
) -> dict[str, Any] | None:
    if catalog is None or not revision:
        return None
    try:
        selected = catalog.read("user", scenario_id, _ui_journeys)
    except (FileNotFoundError, ScenarioCatalogError, ValueError):
        return None
    expected_faults = [] if fault_type == "none" else [fault_type]
    identity = selected["identity"]
    matches = (
        selected["revision"] == revision
        and identity["id"] == scenario_id
        and identity["name"] == name
        and identity["description"] == description
        and identity["tags"] == tags
        and selected["targetService"] == target_service
        and selected["targetServicePort"] in {None, target_service_port}
        and selected["journeys"] == journeys
        and selected["requiredSignals"] == required_signals
        and selected["agentMode"] == agent_mode
        and selected.get("agentExclusions", []) == agent_exclusions
        and selected["configuredFaults"] == expected_faults
    )
    return selected if matches else None


def _ui_journeys(raw: str, *, workspace: Path | None = None) -> list[dict[str, Any]]:
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("Traffic journeys must be valid JSON") from exc
    if not isinstance(value, list) or not value:
        raise ValueError("At least one traffic journey is required")
    journeys: list[dict[str, Any]] = []
    adapters: set[str] = set()
    for index, item in enumerate(value, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"Traffic journey {index} must be a JSON object")
        journey = dict(item)
        name = str(journey.get("name", "")).strip()
        path = str(journey.get("path", "")).strip()
        method = str(journey.get("method", "GET")).upper()
        expected_status = journey.get("expectedStatus")
        if not name:
            raise ValueError(f"Traffic journey {index} requires a name")
        if not path.startswith("/"):
            raise ValueError(f"Traffic journey {index} path must start with /")
        if not method:
            raise ValueError(f"Traffic journey {index} requires an HTTP method")
        if not isinstance(expected_status, int) or isinstance(expected_status, bool):
            raise ValueError(f"Traffic journey {index} expectedStatus must be an integer")
        if expected_status < 100 or expected_status > 599:
            raise ValueError(f"Traffic journey {index} expectedStatus must be between 100 and 599")
        adapter = str(journey.get("adapter", "http"))
        encoding = str(journey.get("requestEncoding", "json" if "body" in journey else "none"))
        if encoding not in {"none", "json", "multipart", "form", "raw"}:
            raise ValueError(
                f"Traffic journey {index} requestEncoding must be "
                "none, json, multipart, form, or raw"
            )
        journey["requestEncoding"] = encoding
        adapters.add(adapter)
        if adapter == "http":
            journey.pop("adapter", None)
            _validate_http_load(journey, index=index)
            _validate_request_encoding(journey, index=index)
        elif adapter == "relayna":
            if encoding not in {"json", "multipart"}:
                raise ValueError(
                    f"Traffic journey {index} Relayna adapter supports JSON or multipart requests"
                )
            if encoding == "multipart":
                _validate_request_encoding(journey, index=index)
            validate_relayna_journey(journey, workspace=workspace)
        else:
            raise ValueError(f"Traffic journey {index} adapter must be http or relayna")
        _validate_follow_ups(journey, index=index)
        journey["name"] = name
        journey["method"] = method
        journey["path"] = path
        journeys.append(journey)
    if len(adapters) > 1:
        raise ValueError("One assessment cannot mix HTTP and Relayna traffic journeys")
    return journeys


async def _persist_journey_files(
    workspace: Path,
    raw: str,
    uploads: list[UploadFile],
    *,
    path_secret: bytes | None = None,
) -> tuple[str, Path | None]:
    if not raw.strip():
        return raw, None
    try:
        journeys = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("Traffic journeys must be valid JSON") from exc
    if not isinstance(journeys, list):
        raise ValueError("Traffic journeys must be a JSON array")
    upload_root = workspace.resolve() / "uploads"
    file_entries: list[dict[str, Any]] = []
    for journey in journeys:
        if not isinstance(journey, dict):
            continue
        multipart = journey.get("multipart")
        if not isinstance(multipart, dict):
            continue
        files = multipart.get("files")
        if not isinstance(files, list):
            continue
        file_entries.extend(item for item in files if isinstance(item, dict))
    if not uploads:
        for item in file_entries:
            if item.pop("uploadIndex", None) is not None:
                raise ValueError("UI multipart files require a browser upload")
            token = item.pop("pathToken", None)
            required = item.get("required", True)
            if not isinstance(required, bool):
                raise ValueError("Multipart file required state must be true or false")
            raw_path = item.get("path")
            if not raw_path and not token and not required:
                item.pop("filename", None)
                item.pop("contentType", None)
                continue
            managed_path = _redeem_multipart_path(raw_path, token, path_secret, upload_root)
            if managed_path is None:
                raise ValueError("UI multipart files require a browser upload")
            item["path"] = str(managed_path)
        return json.dumps(journeys) if file_entries else raw, None
    upload_dir = upload_root / uuid.uuid4().hex
    upload_dir.mkdir(parents=True, exist_ok=False)
    referenced: set[int] = set()
    total_size = 0
    try:
        for item in file_entries:
            required = item.get("required", True)
            if not isinstance(required, bool):
                raise ValueError("Multipart file required state must be true or false")
            token = item.pop("pathToken", None)
            upload_index = item.pop("uploadIndex", None)
            if upload_index is None:
                raw_path = item.get("path")
                managed_path = _redeem_multipart_path(raw_path, token, path_secret, upload_root)
                if managed_path is not None:
                    item["path"] = str(managed_path)
                    continue
                if not raw_path and not token and not required:
                    item.pop("filename", None)
                    item.pop("contentType", None)
                    continue
                raise ValueError("Multipart file is missing its browser upload reference")
            item.pop("path", None)
            if not isinstance(upload_index, int) or isinstance(upload_index, bool):
                raise ValueError("Multipart file is missing its browser upload reference")
            if upload_index < 0 or upload_index >= len(uploads):
                raise ValueError("Multipart file references an unavailable browser upload")
            if upload_index in referenced:
                raise ValueError("Multipart browser upload cannot be reused")
            referenced.add(upload_index)
            upload = uploads[upload_index]
            original_filename = Path(upload.filename or "").name
            configured_filename = item.get("filename")
            filename = str(configured_filename or original_filename).strip()
            if not filename:
                raise ValueError("Browser-uploaded journey files require a filename")
            if Path(filename).name != filename or any(
                character in filename for character in "\r\n"
            ):
                raise ValueError("Browser-uploaded journey filenames must not contain paths")
            content_type = str(item.get("contentType") or upload.content_type or "").strip()
            if not content_type:
                raise ValueError(
                    f"Browser-uploaded journey file {filename!r} requires a content type"
                )
            destination = upload_dir / f"{upload_index}-{filename}"
            size = 0
            with destination.open("wb") as stream:
                while chunk := await upload.read(1024 * 1024):
                    size += len(chunk)
                    if size > MAX_UI_UPLOAD_BYTES:
                        raise ValueError("Browser-uploaded journey files are limited to 128 MiB")
                    total_size += len(chunk)
                    if total_size > MAX_UI_TOTAL_UPLOAD_BYTES:
                        raise ValueError(
                            "Browser-uploaded journey files are limited to 256 MiB total"
                        )
                    stream.write(chunk)
            if size == 0:
                raise ValueError(f"Multipart file {filename!r} must not be empty")
            item.update(
                {
                    "path": str(destination),
                    "filename": filename,
                    "contentType": content_type,
                    "required": required,
                }
            )
        if referenced != set(range(len(uploads))):
            raise ValueError(
                "Every browser-uploaded journey file must belong to a multipart request"
            )
    except Exception:
        shutil.rmtree(upload_dir, ignore_errors=True)
        raise
    return json.dumps(journeys), upload_dir


def _authorize_multipart_paths(
    projection: dict[str, Any], secret: bytes, upload_root: Path
) -> None:
    for journey in projection.get("journeys", []):
        if not isinstance(journey, dict):
            continue
        multipart = journey.get("multipart")
        if not isinstance(multipart, dict):
            continue
        files = multipart.get("files")
        if not isinstance(files, list):
            continue
        for item in files:
            if not isinstance(item, dict):
                continue
            item.pop("pathToken", None)
            managed_path = _managed_multipart_path(item.get("path"), upload_root)
            if managed_path is not None:
                item["pathToken"] = _multipart_path_token(managed_path, secret)


def _redeem_multipart_path(
    path: Any, token: Any, secret: bytes | None, upload_root: Path
) -> Path | None:
    if not isinstance(path, str) or not isinstance(token, str) or secret is None:
        return None
    managed_path = _managed_multipart_path(path, upload_root)
    if managed_path is None:
        return None
    if not secrets.compare_digest(token, _multipart_path_token(managed_path, secret)):
        return None
    return managed_path


def _managed_multipart_path(path: Any, upload_root: Path) -> Path | None:
    if not isinstance(path, str):
        return None
    try:
        resolved = Path(path).resolve(strict=True)
        root = upload_root.resolve(strict=True)
    except (OSError, RuntimeError):
        return None
    if not root.is_dir() or not resolved.is_relative_to(root) or not resolved.is_file():
        return None
    return resolved


def _multipart_path_token(path: Path, secret: bytes) -> str:
    return hmac.new(secret, str(path).encode(), hashlib.sha256).hexdigest()


def _validate_request_encoding(journey: dict[str, Any], *, index: int) -> None:
    encoding = str(journey["requestEncoding"])
    if encoding == "multipart":
        multipart = journey.get("multipart")
        if not isinstance(multipart, dict):
            raise ValueError(f"Traffic journey {index} multipart must be a JSON object")
        fields = multipart.get("fields", {})
        if not isinstance(fields, dict):
            raise ValueError(f"Traffic journey {index} multipart fields must be a JSON object")
        files = multipart.get("files")
        if not isinstance(files, list) or not files:
            raise ValueError(f"Traffic journey {index} multipart requires at least one file")
        seen_fields: set[str] = set()
        for file_index, item in enumerate(files, start=1):
            if not isinstance(item, dict):
                raise ValueError(
                    f"Traffic journey {index} multipart file {file_index} must be a JSON object"
                )
            field = item.get("field")
            path = item.get("path")
            if not isinstance(field, str) or not field.strip():
                raise ValueError(
                    f"Traffic journey {index} multipart file {file_index} requires field"
                )
            if field in seen_fields:
                raise ValueError(f"Traffic journey {index} multipart file fields must be unique")
            seen_fields.add(field)
            required = item.get("required", True)
            if not isinstance(required, bool):
                raise ValueError(
                    f"Traffic journey {index} multipart file {file_index} required "
                    "must be a boolean"
                )
            if (path is None or path == "") and not required:
                continue
            if not isinstance(path, str) or not Path(path).is_file():
                raise ValueError(
                    f"Traffic journey {index} multipart file {file_index} path is not readable"
                )
    elif encoding == "form":
        if not isinstance(journey.get("form"), dict):
            raise ValueError(f"Traffic journey {index} form must be a JSON object")
    elif encoding == "raw":
        if not isinstance(journey.get("body"), str):
            raise ValueError(f"Traffic journey {index} raw body must be a string")
        content_type = journey.get("contentType")
        if not isinstance(content_type, str) or not content_type.strip():
            raise ValueError(f"Traffic journey {index} raw request requires contentType")


def _validate_http_load(journey: dict[str, Any], *, index: int) -> None:
    stages = journey.get("stages")
    if stages is not None:
        if not isinstance(stages, list) or not stages:
            raise ValueError(f"Traffic journey {index} stages must be a non-empty array")
        for stage_index, stage in enumerate(stages, start=1):
            if not isinstance(stage, dict):
                raise ValueError(
                    f"Traffic journey {index} stage {stage_index} must be a JSON object"
                )
            duration = stage.get("duration")
            target_vus = stage.get("targetVus")
            if not isinstance(duration, str) or not duration.strip():
                raise ValueError(f"Traffic journey {index} stage {stage_index} requires a duration")
            if not isinstance(target_vus, int) or isinstance(target_vus, bool) or target_vus < 0:
                raise ValueError(
                    f"Traffic journey {index} stage {stage_index} targetVus "
                    "must be a non-negative integer"
                )
    for field in ("vus", "iterations", "durationSeconds"):
        value = journey.get(field)
        if value is not None and (
            not isinstance(value, int) or isinstance(value, bool) or value <= 0
        ):
            raise ValueError(f"Traffic journey {index} {field} must be a positive integer")
    text_bytes = journey.get("textBytes")
    if text_bytes is not None and (
        not isinstance(text_bytes, int) or isinstance(text_bytes, bool) or text_bytes < 0
    ):
        raise ValueError(f"Traffic journey {index} textBytes must be a non-negative integer")


def _validate_follow_ups(journey: dict[str, Any], *, index: int) -> None:
    follow_ups = journey.get("followUps")
    if follow_ups is None:
        return
    if not isinstance(follow_ups, list) or not follow_ups:
        raise ValueError(f"Traffic journey {index} followUps must be a non-empty array")
    for follow_up_index, follow_up in enumerate(follow_ups, start=1):
        if not isinstance(follow_up, dict):
            raise ValueError(
                f"Traffic journey {index} follow-up {follow_up_index} must be a JSON object"
            )
        for field in ("name", "type", "target", "expected"):
            value = follow_up.get(field)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(
                    f"Traffic journey {index} follow-up {follow_up_index} requires {field}"
                )


def _write_draft(workspace: Path, config: dict[str, Any]) -> Path:
    drafts = workspace / "drafts"
    drafts.mkdir(parents=True, exist_ok=True)
    path = drafts / f"chamber-{uuid.uuid4().hex}.yaml"
    payload = yaml.safe_dump(config, sort_keys=False)
    temporary = path.with_suffix(".yaml.tmp")
    temporary.write_text(payload, encoding="utf-8")
    temporary.replace(path)
    return path


def _safe_config_path(workspace: Path, value: str) -> Path:
    path = Path(value).resolve()
    allowed = (workspace.resolve(), Path.cwd().resolve())
    if not any(path.is_relative_to(root) for root in allowed) or not path.is_file():
        raise HTTPException(status_code=400, detail="config path is outside allowed roots")
    return path


def _check_csrf(request: Request, supplied: str | None) -> None:
    expected = request.cookies.get(CSRF_COOKIE) or getattr(request.state, "csrf_token", None)
    if not supplied or not expected or not secrets.compare_digest(supplied, expected):
        raise HTTPException(status_code=403, detail="invalid CSRF token")


def _capabilities(settings: DiscoverySettings | None = None) -> dict[str, Any]:
    selected_settings = settings or DiscoverySettings.from_environment()
    tools = {name: bool(shutil.which(name)) for name in ("kubectl", "kind", "k6", "docker")}
    return {
        "schema_version": "chamber.ampule.dev/capabilities/v1",
        "tools": tools,
        "runtime_modes": ["local", "kubernetes-deploy", "kubernetes-attach"],
        "traffic_adapters": ["k6", "relayna"],
        "fault_adapters": ["pod_kill", "deployment_scale"],
        "evidence_adapters": ["kubernetes", "prometheus", "k6", "relayna"],
        "ready_for_kind": all(tools.values()),
        "ready_for_kubernetes_attach": tools["kubectl"],
        "kubernetes_context": selected_settings.context,
        "discovery_namespaces": list(selected_settings.namespaces),
    }


def _public_path(path: str) -> bool:
    return path in {"/healthz", "/readyz", "/login"} or path.startswith("/static/")


def _apply_security_headers(response: Response) -> Response:
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; style-src 'self'; script-src 'self'; "
        "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'"
    )
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Frame-Options"] = "DENY"
    return response


async def _job_event_stream(jobs: AssessmentJobManager, job_id: str) -> Any:
    previous = None
    while True:
        job = jobs.get(job_id)
        rendered = json.dumps(job, sort_keys=True)
        if rendered != previous:
            yield f"event: job\ndata: {rendered}\n\n"
            previous = rendered
        if job["state"] in TERMINAL_JOB_STATES:
            break
        await asyncio.sleep(0.5)


async def _run_event_stream(run_dir: Path) -> Any:
    path = run_dir / "events.jsonl"
    emitted = 0
    while True:
        lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
        for line in lines[emitted:]:
            yield f"event: run\ndata: {line}\n\n"
        emitted = len(lines)
        run = _json_file(run_dir / "run.json")
        if run.get("state") in {"completed", "failed", "cancelled"}:
            break
        await asyncio.sleep(0.5)


def _json_file(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}
