"""Common engine contract and shared kubectl/helm helpers."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Protocol

import yaml

from chaos_ci_runner.cluster import Cluster
from chaos_ci_runner.config import ExperimentSpec
from chaos_ci_runner.shell import run


@dataclass
class ExperimentResult:
    """Outcome of a single experiment run from the engine's perspective."""

    name: str
    engine: str
    started_at: float
    finished_at: float
    status: str
    message: str = ""
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def duration_s(self) -> float:
        return max(0.0, self.finished_at - self.started_at)

    @property
    def succeeded(self) -> bool:
        return self.status == "succeeded"


class ChaosEngine(Protocol):
    """Engines install themselves into a cluster, then run experiments."""

    name: str

    def install(self, cluster: Cluster) -> None: ...

    def run_experiment(self, cluster: Cluster, exp: ExperimentSpec) -> ExperimentResult: ...

    def cleanup(self, cluster: Cluster) -> None: ...


def kubectl_apply(cluster: Cluster, manifest: str | dict[str, Any] | list[dict[str, Any]]) -> None:
    """Apply a manifest blob (yaml string or dict(s)) via `kubectl apply -f -`."""
    if isinstance(manifest, (dict, list)):
        if isinstance(manifest, dict):
            payload = yaml.safe_dump(manifest, sort_keys=False)
        else:
            payload = "\n---\n".join(yaml.safe_dump(m, sort_keys=False) for m in manifest)
    else:
        payload = manifest

    run(
        ["kubectl", "apply", "-f", "-"],
        env=cluster.env,
        input_text=payload,
        timeout=120,
    )


def kubectl_delete(cluster: Cluster, kind: str, name: str, namespace: str | None = None) -> None:
    args = ["kubectl", "delete", kind, name, "--ignore-not-found=true"]
    if namespace:
        args.extend(["-n", namespace])
    run(args, env=cluster.env, check=False, timeout=120)


def kubectl_get_json(
    cluster: Cluster,
    kind: str,
    name: str,
    namespace: str | None = None,
) -> dict[str, Any]:
    args = ["kubectl", "get", kind, name, "-o", "json"]
    if namespace:
        args.extend(["-n", namespace])
    result = run(args, env=cluster.env, check=False, timeout=30)
    if result.returncode != 0:
        return {}
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return {}


def wait_for(
    predicate,
    *,
    timeout_s: float,
    interval_s: float = 2.0,
    description: str = "condition",
) -> bool:
    """Poll `predicate()` until truthy or timeout."""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(interval_s)
    return False


def wait_for_deployment_ready(
    cluster: Cluster,
    *,
    namespace: str,
    timeout_s: int,
) -> None:
    """Wait until Deployments, StatefulSets, and DaemonSets have rolled out.

    A namespace with none of those resources returns immediately. A
    StatefulSet used to be ignored, so chaos could start before it was Ready.
    """
    for kind in ("deployment", "statefulset", "daemonset"):
        have = run(
            ["kubectl", "get", kind, "-n", namespace, "-o", "name"],
            env=cluster.env,
            check=False,
            timeout=15,
        )
        if have.returncode != 0 or not have.stdout.strip():
            continue
        for name in have.stdout.split():
            run(
                [
                    "kubectl",
                    "rollout",
                    "status",
                    name,
                    "-n",
                    namespace,
                    f"--timeout={timeout_s}s",
                ],
                env=cluster.env,
                timeout=timeout_s + 30,
            )
