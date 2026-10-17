"""Chaos Mesh engine adapter.

Installs Chaos Mesh once per cluster via Helm, then applies one CRD per
experiment (PodChaos, NetworkChaos, StressChaos, ...). The CRD's `kind`
defaults to PodChaos when the spec doesn't declare one.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from chaos_ci_runner.cluster import Cluster
from chaos_ci_runner.config import ExperimentSpec
from chaos_ci_runner.engines.base import (
    ExperimentResult,
    kubectl_apply,
    kubectl_delete,
    kubectl_get_json,
)
from chaos_ci_runner.shell import require, run

log = logging.getLogger("chaos_ci_runner")

CHAOS_MESH_NAMESPACE = "chaos-mesh"
CHAOS_MESH_RELEASE = "chaos-mesh"
CHAOS_MESH_REPO = "https://charts.chaos-mesh.org"
CHAOS_MESH_VERSION = "2.7.0"
CHAOS_MESH_API_GROUP = "chaos-mesh.org/v1alpha1"

DEFAULT_KIND = "PodChaos"


def chaos_mesh_helm_args(*, version: str, chart_repo: str) -> list[str]:
    """Helm install that fetches one chart index, not every cached repo."""
    return [
        "helm",
        "upgrade",
        "--install",
        CHAOS_MESH_RELEASE,
        "chaos-mesh",
        "--repo",
        chart_repo,
        "--namespace",
        CHAOS_MESH_NAMESPACE,
        "--create-namespace",
        "--version",
        version,
        "--set",
        "chaosDaemon.runtime=containerd",
        "--set",
        "chaosDaemon.socketPath=/run/k3s/containerd/containerd.sock",
        "--wait",
        "--timeout",
        "5m",
    ]


def chaos_mesh_manifest(exp: ExperimentSpec) -> dict[str, Any]:
    """Chaos Mesh custom resource a run would apply for this experiment."""
    kind = exp.kind or DEFAULT_KIND
    manifest_name = f"ccr-{exp.name.replace('_', '-')}"
    manifest: dict[str, Any] = {
        "apiVersion": CHAOS_MESH_API_GROUP,
        "kind": kind,
        "metadata": {
            "name": manifest_name,
            "namespace": CHAOS_MESH_NAMESPACE,
        },
        "spec": dict(exp.spec or {}),
    }
    if "duration" not in manifest["spec"]:
        manifest["spec"]["duration"] = f"{exp.duration_s}s"
    return manifest


def chaos_mesh_terminal_status(
    *,
    phase: str,
    conditions: dict[str, str],
    injected: bool,
    held_s: float,
    duration_s: int,
    past_injection_deadline: bool,
) -> tuple[str, str] | None:
    """Return (status, message) when the watch should stop, else None.

    A pause before injection is a failure. Chaos Mesh can report Paused
    when the controller rejects the experiment, and that used to count
    as success.
    """
    all_recovered = conditions.get("AllRecovered") == "True"
    paused = conditions.get("Paused") == "True"
    if phase == "Stop" or all_recovered or (paused and injected):
        return "succeeded", ""
    if paused and not injected:
        return (
            "failed",
            f"chaos-mesh paused before injection; phase={phase!r} conditions={conditions}",
        )
    if injected and held_s >= duration_s:
        return "succeeded", ""
    if not injected and past_injection_deadline:
        return (
            "failed",
            f"chaos-mesh did not inject within 60s; phase={phase!r} conditions={conditions}",
        )
    return None


class ChaosMeshEngine:
    name = "chaos-mesh"

    def __init__(
        self,
        *,
        version: str = CHAOS_MESH_VERSION,
        chart_repo: str = CHAOS_MESH_REPO,
    ) -> None:
        self.version = version
        self.chart_repo = chart_repo
        self._installed = False

    def install(self, cluster: Cluster) -> None:
        if self._installed:
            return
        require("helm")
        log.info("installing chaos-mesh %s via helm", self.version)
        run(
            chaos_mesh_helm_args(version=self.version, chart_repo=self.chart_repo),
            env=cluster.env,
            timeout=420,
        )
        # Helm --wait ignores DaemonSets in some versions; explicitly wait
        # for the chaos-controller-manager and chaos-daemon to be ready.
        log.info("waiting for chaos-mesh controller and daemon to be ready")
        run(
            [
                "kubectl",
                "wait",
                "--for=condition=Available",
                "--all",
                "deployment",
                "-n",
                CHAOS_MESH_NAMESPACE,
                "--timeout=180s",
            ],
            env=cluster.env,
            timeout=210,
        )
        run(
            [
                "kubectl",
                "rollout",
                "status",
                "daemonset/chaos-daemon",
                "-n",
                CHAOS_MESH_NAMESPACE,
                "--timeout=180s",
            ],
            env=cluster.env,
            timeout=210,
            check=False,
        )
        self._installed = True

    def run_experiment(self, cluster: Cluster, exp: ExperimentSpec) -> ExperimentResult:
        manifest = chaos_mesh_manifest(exp)
        kind = str(manifest["kind"])
        manifest_name = str(manifest["metadata"]["name"])

        started = time.time()
        log.info("[chaos-mesh] applying %s/%s for %ss", kind, manifest_name, exp.duration_s)
        kubectl_apply(cluster, manifest)

        # Two budgets: a short one for "did the controller pick this up?"
        # and a longer one for "did chaos run for the requested duration?"
        budget_s = exp.duration_s + 90
        injection_deadline = started + 60
        status = "unknown"
        message = ""
        last_obj: dict[str, Any] = {}
        phase = ""
        cond_map: dict[str, str] = {}
        injected_at: float | None = None
        deadline = started + budget_s
        last_log_time = 0.0
        try:
            while time.time() < deadline:
                last_obj = kubectl_get_json(
                    cluster, kind, manifest_name, namespace=CHAOS_MESH_NAMESPACE
                )
                st = last_obj.get("status", {}) if last_obj else {}
                phase = st.get("experiment", {}).get("desiredPhase", "") or st.get("phase", "")
                conditions = st.get("conditions", [])
                cond_map = {c.get("type"): c.get("status") for c in conditions}
                all_injected = cond_map.get("AllInjected") == "True"
                all_recovered = cond_map.get("AllRecovered") == "True"
                paused = cond_map.get("Paused") == "True"

                if all_injected and injected_at is None:
                    injected_at = time.time()
                    log.info(
                        "[chaos-mesh] %s/%s injected; will hold for %ss",
                        kind,
                        manifest_name,
                        exp.duration_s,
                    )

                if time.time() - last_log_time > 10:
                    log.info(
                        "[chaos-mesh] %s/%s phase=%s conditions=%s",
                        kind,
                        manifest_name,
                        phase or "<empty>",
                        cond_map or {},
                    )
                    last_log_time = time.time()

                held_s = 0.0 if injected_at is None else time.time() - injected_at
                decision = chaos_mesh_terminal_status(
                    phase=phase,
                    conditions=cond_map,
                    injected=injected_at is not None,
                    held_s=held_s,
                    duration_s=exp.duration_s,
                    past_injection_deadline=injected_at is None
                    and time.time() > injection_deadline,
                )
                if decision is not None:
                    status, message = decision
                    if status == "failed":
                        log.warning("[chaos-mesh] %s", message)
                    elif (
                        injected_at is not None
                        and held_s >= exp.duration_s
                        and not (phase == "Stop" or all_recovered or paused)
                    ):
                        log.info(
                            "[chaos-mesh] %s/%s held for %ss after injection; marking success",
                            kind,
                            manifest_name,
                            exp.duration_s,
                        )
                    break
                time.sleep(2)
            else:
                status = "timeout"
                message = (
                    f"experiment did not finish within {budget_s}s; "
                    f"last phase={phase!r} conditions={cond_map}"
                )
                log.warning("[chaos-mesh] %s", message)
        finally:
            kubectl_delete(cluster, kind, manifest_name, namespace=CHAOS_MESH_NAMESPACE)
        finished = time.time()
        return ExperimentResult(
            name=exp.name,
            engine=self.name,
            started_at=started,
            finished_at=finished,
            status=status,
            message=message,
            raw=last_obj,
        )

    def cleanup(self, cluster: Cluster) -> None:
        # Cluster is ephemeral; nothing to do beyond what `down()` already removes.
        self._installed = False
