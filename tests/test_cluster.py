"""Cluster teardown removes the temporary kubeconfig directory."""

from __future__ import annotations

from pathlib import Path

from chaos_ci_runner.cluster import Cluster, down


def test_down_removes_kubeconfig_directory(tmp_path: Path, monkeypatch) -> None:
    parent = tmp_path / "ccr-kube"
    parent.mkdir()
    kubeconfig = parent / "kubeconfig"
    kubeconfig.write_text("apiVersion: v1\n", encoding="utf-8")
    monkeypatch.setattr("chaos_ci_runner.cluster.run", lambda *args, **kwargs: None)
    down(Cluster(name="ccr-test", kubeconfig=kubeconfig))
    assert not parent.exists()
