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
CHAOS_MESH_CHART = "chaos-mesh/chaos-mesh"
CHAOS_MESH_VERSION = "2.7.0"
CHAOS_MESH_API_GROUP = "chaos-mesh.org/v1alpha1"

DEFAULT_KIND = "PodChaos"


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
            ["helm", "repo", "add", "chaos-mesh", self.chart_repo],
            check=False,
            timeout=60,
        )
        run(["helm", "repo", "update"], env=cluster.env, timeout=120)
        run(
            [
                "kubectl",
                "apply",
                "-f",
                "-",
            ],
            env=cluster.env,
            input_text=f"apiVersion: v1\nkind: Namespace\nmetadata:\n  name: {CHAOS_MESH_NAMESPACE}\n",
            timeout=30,
        )
        run(
            [
                "helm",
                "upgrade",
                "--install",
                CHAOS_MESH_RELEASE,
                CHAOS_MESH_CHART,
                "--namespace",
                CHAOS_MESH_NAMESPACE,
                "--version",
                self.version,
                "--set",
                "chaosDaemon.runtime=containerd",
                "--set",
                "chaosDaemon.socketPath=/run/k3s/containerd/containerd.sock",
                "--wait",
                "--timeout",
                "5m",
            ],
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
        kind = exp.kind or DEFAULT_KIND
        manifest_name = f"ccr-{exp.name.replace('_', '-')}"
        manifest: dict[str, Any] = {
            "apiVersion": CHAOS_MESH_API_GROUP,
            "kind": kind,
            "metadata": {
                "name": manifest_name,
                "namespace": CHAOS_MESH_NAMESPACE,
            },
            "spec": exp.spec or {},
        }
        if "duration" not in manifest["spec"]:
            manifest["spec"]["duration"] = f"{exp.duration_s}s"

        started = time.time()
        log.info("[chaos-mesh] applying %s/%s for %ss", kind, manifest_name, exp.duration_s)
        kubectl_apply(cluster, manifest)

        budget_s = exp.duration_s + 90
        status = "unknown"
        message = ""
        last_obj: dict[str, Any] = {}
        phase = ""
        cond_map: dict[str, str] = {}
        deadline = started + budget_s
        last_log_time = 0.0
        while time.time() < deadline:
            last_obj = kubectl_get_json(
                cluster, kind, manifest_name, namespace=CHAOS_MESH_NAMESPACE
            )
            st = last_obj.get("status", {}) if last_obj else {}
            phase = st.get("experiment", {}).get("desiredPhase", "") or st.get("phase", "")
            conditions = st.get("conditions", [])
            cond_map = {c.get("type"): c.get("status") for c in conditions}
            all_recovered = cond_map.get("AllRecovered") == "True"
            paused = cond_map.get("Paused") == "True"

            if time.time() - last_log_time > 10:
                log.info(
                    "[chaos-mesh] %s/%s phase=%s conditions=%s",
                    kind,
                    manifest_name,
                    phase or "<empty>",
                    cond_map or {},
                )
                last_log_time = time.time()

            if phase == "Stop" or all_recovered or paused:
                status = "succeeded"
                break
            time.sleep(2)
        else:
            status = "timeout"
            message = (
                f"experiment did not finish within {budget_s}s; "
                f"last phase={phase!r} conditions={cond_map}"
            )
            log.warning("[chaos-mesh] %s", message)

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
