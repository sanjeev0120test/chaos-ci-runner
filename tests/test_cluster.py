"""Cluster teardown removes the temporary kubeconfig directory."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from chaos_ci_runner.cluster import Cluster, down, up
from chaos_ci_runner.shell import CommandError


def test_down_removes_kubeconfig_directory(tmp_path: Path, monkeypatch) -> None:
    parent = tmp_path / "ccr-kube"
    parent.mkdir()
    kubeconfig = parent / "kubeconfig"
    kubeconfig.write_text("apiVersion: v1\n", encoding="utf-8")
    monkeypatch.setattr("chaos_ci_runner.cluster.run", lambda *args, **kwargs: None)
    down(Cluster(name="ccr-test", kubeconfig=kubeconfig))
    assert not parent.exists()


def test_down_swallows_delete_errors(tmp_path: Path, monkeypatch) -> None:
    kubeconfig = tmp_path / "kubeconfig"
    kubeconfig.write_text("apiVersion: v1\n", encoding="utf-8")

    def explode(*args, **kwargs):
        raise CommandError("timed out")

    monkeypatch.setattr("chaos_ci_runner.cluster.run", explode)
    down(Cluster(name="ccr-test", kubeconfig=kubeconfig))
    assert not kubeconfig.exists()


def test_up_deletes_cluster_when_startup_fails(tmp_path: Path, monkeypatch) -> None:
    kube_dir = tmp_path / "kc"
    kube_dir.mkdir()
    deleted: list[str] = []

    monkeypatch.setattr("chaos_ci_runner.cluster.require", lambda tool: tool)
    monkeypatch.setattr("chaos_ci_runner.cluster.tempfile.mkdtemp", lambda prefix: str(kube_dir))

    def fake_run(args, **kwargs):
        if args[:3] == ["k3d", "cluster", "delete"]:
            deleted.append(args[3])
        if args[:2] == ["k3d", "kubeconfig"]:
            output = Path(args[args.index("--output") + 1])
            output.write_text("apiVersion: v1\n", encoding="utf-8")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("chaos_ci_runner.cluster.run", fake_run)

    def not_ready(*args, **kwargs):
        raise TimeoutError("nodes")

    monkeypatch.setattr("chaos_ci_runner.cluster._wait_for_nodes", not_ready)
    with pytest.raises(TimeoutError, match="nodes"):
        up(name="ccr-leak", wait_timeout_s=30)
    assert deleted == ["ccr-leak"]
    assert not (kube_dir / "kubeconfig").exists()
