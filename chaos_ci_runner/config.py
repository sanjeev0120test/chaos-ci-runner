"""Pydantic schema for chaos.yaml.

The schema is intentionally small: a target workload, steady-state probes,
a list of experiments delegated to engines (chaos-mesh or litmus), and a
gate that decides pass/fail.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator


class ClusterSpec(BaseModel):
    """Optional ephemeral cluster knobs.

    `port_mappings` are forwarded to `k3d cluster create --port`. Use this
    when the steady-state probes hit a NodePort from the CI runner
    (e.g. ``["30080:30080@server:0"]``).
    """

    model_config = ConfigDict(extra="forbid")

    image: str = Field("rancher/k3s:v1.30.4-k3s1")
    wait_timeout_s: int = Field(180, ge=30, le=900)
    port_mappings: list[str] = Field(default_factory=list)


class TargetSpec(BaseModel):
    """Workload under test."""

    model_config = ConfigDict(extra="forbid")

    manifest: str = Field(..., description="Path to a Kubernetes manifest (YAML).")
    namespace: str = Field("default", description="Namespace to deploy into.")
    ready_timeout_s: int = Field(60, ge=1, le=900)


class HttpProbeSpec(BaseModel):
    """HTTP-based steady-state probe."""

    model_config = ConfigDict(extra="forbid")

    name: str
    url: str
    success_rate_pct: float = Field(99.0, ge=0.0, le=100.0)
    latency_p99_ms: int = Field(1000, ge=1)
    duration_s: int = Field(15, ge=1, le=600)
    interval_ms: int = Field(200, ge=10, le=10_000)
    timeout_ms: int = Field(2_000, ge=10, le=30_000)


class SteadyStateSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    http_probes: list[HttpProbeSpec] = Field(default_factory=list)


class ExperimentSpec(BaseModel):
    """A single chaos experiment.

    `engine="chaos-mesh"` => `spec` is the `spec` of a Chaos Mesh CRD
        (e.g., PodChaos, NetworkChaos). The runner wraps it with the right
        apiVersion/kind based on `kind`.
    `engine="litmus"` => `experiment` is a litmuschaos experiment name
        (e.g., "pod-network-latency") and `params` are passed as env vars
        to the experiment Job.
    """

    model_config = ConfigDict(extra="forbid")

    name: str
    engine: Literal["chaos-mesh", "litmus"]
    duration_s: int = Field(30, ge=1, le=900)

    kind: str | None = Field(
        default=None,
        description="Chaos Mesh CRD kind, e.g. PodChaos, NetworkChaos. Defaults to PodChaos.",
    )
    spec: dict[str, Any] | None = Field(
        default=None,
        description="Raw `spec` body for the Chaos Mesh CRD.",
    )

    experiment: str | None = Field(
        default=None,
        description="LitmusChaos experiment name, e.g. pod-network-latency.",
    )
    params: dict[str, str] = Field(
        default_factory=dict,
        description="Env-var style params for LitmusChaos experiments.",
    )
    app_label: str | None = Field(
        default=None,
        description="Litmus target label selector, e.g. 'app=nginx'. Required for litmus.",
    )

    @field_validator("kind")
    @classmethod
    def _normalize_kind(cls, v: str | None) -> str | None:
        return v if v else None

    def validate_engine_fields(self) -> None:
        """Cross-field invariants per engine."""
        if self.engine == "chaos-mesh" and not self.spec:
            raise ValueError(f"experiment '{self.name}': chaos-mesh engine requires `spec`")
        if self.engine == "litmus":
            if not self.experiment:
                raise ValueError(f"experiment '{self.name}': litmus engine requires `experiment`")
            if not self.app_label:
                raise ValueError(
                    f"experiment '{self.name}': litmus engine requires `app_label` (e.g. 'app=nginx')"
                )


class GateSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    min_pass_rate_pct: float = Field(100.0, ge=0.0, le=100.0)
    fail_on_probe_breach: bool = True


class ChaosConfig(BaseModel):
    """Top-level chaos.yaml schema."""

    model_config = ConfigDict(extra="forbid")

    cluster: ClusterSpec = Field(default_factory=ClusterSpec)
    target: TargetSpec
    steady_state: SteadyStateSpec = Field(default_factory=SteadyStateSpec)
    experiments: list[ExperimentSpec] = Field(..., min_length=1)
    gate: GateSpec = Field(default_factory=GateSpec)

    def model_post_init(self, _ctx: Any) -> None:
        for exp in self.experiments:
            exp.validate_engine_fields()


def load_config(path: str | Path) -> ChaosConfig:
    """Load and validate a chaos.yaml file.

    Raises pydantic.ValidationError or ValueError with a precise message.
    """
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"chaos config not found: {p}")
    raw = yaml.safe_load(p.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"{p}: top-level YAML must be a mapping")
    return ChaosConfig.model_validate(raw)
