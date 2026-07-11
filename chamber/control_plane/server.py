"""FastAPI adapter and server-rendered local control plane."""

from __future__ import annotations

import asyncio
import json
import secrets
import shutil
import threading
import uuid
import webbrowser
from dataclasses import asdict
from pathlib import Path
from typing import Annotated, Any, cast

import yaml
from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
from starlette.responses import StreamingResponse

from chamber.application.service import ChamberApplication
from chamber.control_plane.jobs import TERMINAL_JOB_STATES, AssessmentJobManager
from chamber.runs import registered_evidence

PACKAGE_DIR = Path(__file__).resolve().parent
TEMPLATE_DIR = PACKAGE_DIR / "templates"
STATIC_DIR = PACKAGE_DIR / "static"
CSRF_COOKIE = "ampule_csrf"


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


def create_app(workspace: Path = Path(".chamber")) -> FastAPI:
    """Create the local control-plane application."""

    application = ChamberApplication(workspace)
    application.initialize()
    jobs = AssessmentJobManager(workspace)
    templates = Jinja2Templates(directory=TEMPLATE_DIR)
    app = FastAPI(title="Ampule Chamber Control Plane", version="1")
    app.state.chamber = application
    app.state.jobs = jobs
    app.state.templates = templates
    app.state.workspace = workspace
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.middleware("http")
    async def security_headers(request: Request, call_next: Any) -> Response:
        token = request.cookies.get(CSRF_COOKIE) or secrets.token_urlsafe(32)
        request.state.csrf_token = token
        response = await call_next(request)
        if request.cookies.get(CSRF_COOKIE) is None:
            response.set_cookie(
                CSRF_COOKIE,
                token,
                httponly=False,
                samesite="strict",
                secure=False,
            )
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; style-src 'self'; script-src 'self'; "
            "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'"
        )
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Frame-Options"] = "DENY"
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
                "capabilities": _capabilities(),
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
        traffic_path: Annotated[str, Form()] = "/health",
        traffic_profile: Annotated[str, Form()] = "baseline",
        fault_type: Annotated[str, Form()] = "none",
        prometheus_url: Annotated[str, Form()] = "",
        agents_mode: Annotated[str, Form()] = "offline",
    ) -> Response:
        _check_csrf(request, csrf)
        try:
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
                traffic_path=traffic_path,
                traffic_profile=traffic_profile,
                fault_type=fault_type,
                prometheus_url=prometheus_url,
                agents_mode=agents_mode,
            )
        except (OSError, ValueError, RuntimeError) as exc:
            return templates.TemplateResponse(
                request=request,
                name="new.html",
                status_code=400,
                context={
                    "active_nav": "new",
                    "csrf_token": request.state.csrf_token,
                    "capabilities": _capabilities(),
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
        return _capabilities()

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
        report_path = application.report(run_dir)
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
    traffic_path: str,
    traffic_profile: str,
    fault_type: str,
    prometheus_url: str,
    agents_mode: str,
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
            "stages": [{"duration": duration, "targetVus": target} for duration, target in stages],
        }
    ]
    config["agents"] = {"mode": agents_mode}
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
    config_path = _write_draft(workspace, config)
    return application.plan(config_path)


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


def _capabilities() -> dict[str, Any]:
    tools = {name: bool(shutil.which(name)) for name in ("kubectl", "kind", "k6", "docker")}
    return {
        "schema_version": "chamber.ampule.dev/capabilities/v1",
        "tools": tools,
        "runtime_modes": ["local", "kubernetes-deploy", "kubernetes-attach"],
        "traffic_adapters": ["k6"],
        "fault_adapters": ["pod_kill", "deployment_scale"],
        "evidence_adapters": ["kubernetes", "prometheus", "k6"],
        "ready_for_kind": all(tools.values()),
    }


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
