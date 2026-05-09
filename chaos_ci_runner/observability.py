"""Observability stack lifecycle (v2).

Installs a minimal Prometheus + kube-state-metrics into the ephemeral
cluster via the `prometheus-community/prometheus` Helm chart. Exposes
Prometheus on a NodePort so PromQL probes can hit it from the runner.

We deliberately skip Grafana, Alertmanager, and pushgateway: chaos-ci-
runner is the consumer, and a markdown report is the user-facing UI.
"""

from __future__ import annotations

import logging

from chaos_ci_runner.cluster import Cluster
from chaos_ci_runner.shell import require, run

log = logging.getLogger("chaos_ci_runner")

PROM_NAMESPACE = "monitoring"
PROM_RELEASE = "ccr-prom"
PROM_REPO = "https://prometheus-community.github.io/helm-charts"
PROM_CHART = "prometheus-community/prometheus"
PROM_VERSION = "27.5.1"
PROM_NODEPORT = 30090
PROM_SERVICE = "ccr-prom-prometheus-server"


class PrometheusInstaller:
    """Install Prometheus + kube-state-metrics into the cluster."""

    def __init__(
        self,
        *,
        version: str = PROM_VERSION,
        node_port: int = PROM_NODEPORT,
    ) -> None:
        self.version = version
        self.node_port = node_port
        self._installed = False

    def install(self, cluster: Cluster) -> None:
        if self._installed:
            return
        require("helm")
        log.info("installing prometheus %s via helm", self.version)
        run(
            ["helm", "repo", "add", "prometheus-community", PROM_REPO],
            check=False,
            timeout=60,
        )
        run(["helm", "repo", "update"], env=cluster.env, timeout=120)
        run(
            ["kubectl", "apply", "-f", "-"],
            env=cluster.env,
            input_text=f"apiVersion: v1\nkind: Namespace\nmetadata:\n  name: {PROM_NAMESPACE}\n",
            timeout=30,
        )
        run(
            [
                "helm",
                "upgrade",
                "--install",
                PROM_RELEASE,
                PROM_CHART,
                "--namespace",
                PROM_NAMESPACE,
                "--version",
                self.version,
                "--set",
                "alertmanager.enabled=false",
                "--set",
                "prometheus-pushgateway.enabled=false",
                "--set",
                "server.service.type=NodePort",
                "--set",
                f"server.service.nodePort={self.node_port}",
                "--set",
                "server.persistentVolume.enabled=false",
                "--set",
                "server.global.scrape_interval=10s",
                "--wait",
                "--timeout",
                "5m",
            ],
            env=cluster.env,
            timeout=420,
        )
        log.info("waiting for prometheus and kube-state-metrics to be Ready")
        run(
            [
                "kubectl",
                "wait",
                "--for=condition=Available",
                "--all",
                "deployment",
                "-n",
                PROM_NAMESPACE,
                "--timeout=180s",
            ],
            env=cluster.env,
            timeout=210,
            check=False,
        )
        self._installed = True

    @property
    def base_url(self) -> str:
        return f"http://localhost:{self.node_port}"

    @property
    def required_port_mapping(self) -> str:
        """k3d port mapping caller must include in cluster.port_mappings."""
        return f"{self.node_port}:{self.node_port}@server:0"
