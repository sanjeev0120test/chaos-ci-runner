"""Helm install lines stay narrow: one chart repo, no extra daemons."""

from __future__ import annotations

from chaos_ci_runner.engines.chaos_mesh import CHAOS_MESH_REPO, chaos_mesh_helm_args
from chaos_ci_runner.observability import prometheus_helm_args


def test_chaos_mesh_helm_skips_repo_update() -> None:
    args = chaos_mesh_helm_args(version="2.7.0", chart_repo=CHAOS_MESH_REPO)
    assert args[:5] == ["helm", "upgrade", "--install", "chaos-mesh", "chaos-mesh"]
    assert args[args.index("--repo") + 1] == CHAOS_MESH_REPO
    assert "2.7.0" in args
    assert "--create-namespace" in args
    joined = " ".join(args)
    assert "repo update" not in joined
    assert "containerd.sock" in joined


def test_prometheus_helm_disables_unused_components() -> None:
    args = prometheus_helm_args(version="27.5.1", node_port=30090)
    assert args[3] == "ccr-prom"
    assert "prometheus" in args
    assert "27.5.1" in args
    assert "prometheus-node-exporter.enabled=false" in args
    assert "alertmanager.enabled=false" in args
    assert "prometheus-pushgateway.enabled=false" in args
    assert "server.service.nodePort=30090" in args
    assert "server.persistentVolume.enabled=false" in args
