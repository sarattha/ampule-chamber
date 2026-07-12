"""Validate the checked-in Kubernetes and Helm deployment contracts."""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
RAW_MANIFEST = ROOT / "deploy/kubernetes/ampule-chamber.yaml"
CHART = ROOT / "deploy/helm/ampule-chamber/Chart.yaml"
VALUES = ROOT / "deploy/helm/ampule-chamber/values.yaml"


def main() -> int:
    version = _project_version()
    chart = _load_mapping(CHART)
    values = _load_mapping(VALUES)
    manifests = list(yaml.safe_load_all(RAW_MANIFEST.read_text(encoding="utf-8")))

    if chart.get("version") != version or str(chart.get("appVersion")) != version:
        raise SystemExit("Helm chart version and appVersion must match project.version")
    image = _mapping(values, "image")
    if str(image.get("tag")) != version:
        raise SystemExit("Helm image.tag must match project.version")
    if values.get("replicaCount") != 1:
        raise SystemExit("Helm replicaCount must remain 1 for the PVC-backed control plane")
    persistence = _mapping(values, "persistence")
    if not persistence.get("enabled"):
        raise SystemExit("Helm persistence must be enabled by default")
    if not persistence.get("retain"):
        raise SystemExit("Helm persistence must be retained by default")

    resources = {
        (item.get("apiVersion"), item.get("kind")) for item in manifests if isinstance(item, dict)
    }
    required = {
        ("v1", "Namespace"),
        ("v1", "ServiceAccount"),
        ("v1", "Secret"),
        ("v1", "ConfigMap"),
        ("v1", "PersistentVolumeClaim"),
        ("rbac.authorization.k8s.io/v1", "ClusterRole"),
        ("rbac.authorization.k8s.io/v1", "ClusterRoleBinding"),
        ("rbac.authorization.k8s.io/v1", "Role"),
        ("rbac.authorization.k8s.io/v1", "RoleBinding"),
        ("apps/v1", "Deployment"),
        ("v1", "Service"),
    }
    missing = sorted(required - resources)
    if missing:
        raise SystemExit(f"raw manifest is missing required resources: {missing}")

    deployment = next(
        item for item in manifests if isinstance(item, dict) and item.get("kind") == "Deployment"
    )
    spec = _mapping(deployment, "spec")
    if spec.get("replicas") != 1 or _mapping(spec, "strategy").get("type") != "Recreate":
        raise SystemExit("raw Deployment must use one replica and Recreate strategy")
    template_spec = _mapping(_mapping(spec, "template"), "spec")
    containers = template_spec.get("containers")
    if not isinstance(containers, list) or not containers:
        raise SystemExit("raw Deployment must define a control-plane container")
    container = containers[0]
    if not isinstance(container, dict):
        raise SystemExit("raw Deployment container must be a mapping")
    expected_image = f"ghcr.io/sarattha/ampule-chamber:{version}"
    if container.get("image") != expected_image:
        raise SystemExit(f"raw Deployment image must be {expected_image}")
    if not _mapping(container, "securityContext").get("readOnlyRootFilesystem"):
        raise SystemExit("raw Deployment must use a read-only root filesystem")
    mounts = container.get("volumeMounts")
    if not isinstance(mounts, list) or not any(
        isinstance(mount, dict) and mount.get("mountPath") == "/data" for mount in mounts
    ):
        raise SystemExit("raw Deployment must mount persistent workspace storage at /data")

    print(f"deployment metadata ok: {version}")
    return 0


def _project_version() -> str:
    payload = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    version = payload["project"]["version"]
    if not isinstance(version, str):
        raise SystemExit("project.version must be a string")
    return version


def _load_mapping(path: Path) -> dict[str, Any]:
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise SystemExit(f"{path.relative_to(ROOT)} must contain a YAML mapping")
    return payload


def _mapping(payload: dict[str, Any], key: str) -> dict[str, Any]:
    value = payload.get(key)
    if not isinstance(value, dict):
        raise SystemExit(f"{key} must be a mapping")
    return value


if __name__ == "__main__":
    raise SystemExit(main())
