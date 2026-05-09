"""Tests for engine helpers that don't require a real cluster."""

from __future__ import annotations

import pytest

from chaos_ci_runner.engines.litmus import _experiment_job, _rbac_manifests, _split_label


def test_split_label_valid() -> None:
    assert _split_label("app=nginx") == ("app", "nginx")


def test_split_label_invalid() -> None:
    with pytest.raises(ValueError):
        _split_label("nginx")


def test_rbac_manifests_shape() -> None:
    out = _rbac_manifests("litmus", "litmus-experiment-sa")
    kinds = {m["kind"] for m in out}
    assert kinds == {"ServiceAccount", "ClusterRole", "ClusterRoleBinding"}


def test_experiment_job_has_required_env() -> None:
    job = _experiment_job(
        name="job",
        namespace="litmus",
        sa="litmus-experiment-sa",
        image="litmuschaos/go-runner:3.10.0",
        experiment="pod-network-latency",
        params={"TOTAL_CHAOS_DURATION": "30", "APP_NAMESPACE": "default"},
        label_key="app",
        label_value="nginx",
    )
    assert job["kind"] == "Job"
    env = {e["name"]: e["value"] for e in job["spec"]["template"]["spec"]["containers"][0]["env"]}
    assert env["EXPERIMENT_NAME"] == "pod-network-latency"
    assert env["APP_LABEL_KEY"] == "app"
    assert env["APP_LABEL_VALUE"] == "nginx"
    assert env["TOTAL_CHAOS_DURATION"] == "30"
