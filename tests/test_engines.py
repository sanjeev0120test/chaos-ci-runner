"""Tests for engine helpers that don't require a real cluster."""

from __future__ import annotations

import pytest

from chaos_ci_runner.config import ExperimentSpec
from chaos_ci_runner.engines.chaos_mesh import chaos_mesh_manifest
from chaos_ci_runner.engines.litmus import (
    _experiment_job,
    _rbac_manifests,
    _split_label,
    litmus_job_manifest,
)


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


def test_chaos_mesh_manifest_sets_duration_and_name() -> None:
    exp = ExperimentSpec(
        name="kill_one",
        engine="chaos-mesh",
        duration_s=12,
        spec={"action": "pod-kill", "mode": "one"},
    )
    doc = chaos_mesh_manifest(exp)
    assert doc["kind"] == "PodChaos"
    assert doc["metadata"]["name"] == "ccr-kill-one"
    assert doc["spec"]["duration"] == "12s"


def test_chaos_mesh_manifest_keeps_explicit_duration() -> None:
    exp = ExperimentSpec(
        name="kill",
        engine="chaos-mesh",
        duration_s=12,
        spec={"action": "pod-kill", "mode": "one", "duration": "5s"},
    )
    assert chaos_mesh_manifest(exp)["spec"]["duration"] == "5s"


def test_litmus_job_manifest_requires_fields() -> None:
    missing_experiment = ExperimentSpec(name="lat", engine="litmus")
    with pytest.raises(ValueError, match="requires `experiment`"):
        litmus_job_manifest(missing_experiment, job_name="job")
    missing_label = ExperimentSpec(name="lat", engine="litmus", experiment="pod-delete")
    with pytest.raises(ValueError, match="app_label"):
        litmus_job_manifest(missing_label, job_name="job")
