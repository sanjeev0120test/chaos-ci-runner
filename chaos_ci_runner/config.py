"""Pydantic schema for chaos.yaml.

The schema is intentionally small: a target workload, steady-state probes,
a list of experiments delegated to engines (chaos-mesh or litmus), and a
gate that decides pass/fail.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

# Kubernetes DNS-1123 label: object names, namespaces, and probe names.
_DNS1123 = re.compile(r"^[a-z0-9]([-a-z0-9]*[a-z0-9])?$")
# k3d `--port` value, including the nginx example `30080:30080@server:0`.
_PORT_MAPPING = re.compile(
    r"^(?:(?:\d{1,3}\.){3}\d{1,3}:)?\d{1,5}:\d{1,5}"
    r"(?:/(?:tcp|udp|sctp))?(?:@(?:server|agent|loadbalancer):\d+)?$"
)
_CLUSTER_SCOPED = {
    "Namespace",
    "PersistentVolume",
    "CustomResourceDefinition",
    "ClusterRole",
    "ClusterRoleBinding",
    "StorageClass",
    "Node",
}


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


class PrometheusProbeSpec(BaseModel):
    """PromQL-based steady-state probe (v2).

    Evaluates `query` against Prometheus repeatedly during the probe
    window. A sample is "ok" when the scalar/instant-vector value
    satisfies both bounds (when set). At least one of `max` / `min`
    must be provided.
    """

    model_config = ConfigDict(extra="forbid")

    name: str
    query: str
    max: float | None = None
    min: float | None = None
    duration_s: int = Field(15, ge=1, le=600)
    interval_ms: int = Field(2_000, ge=100, le=60_000)
    timeout_ms: int = Field(5_000, ge=100, le=30_000)
    success_rate_pct: float = Field(
        100.0,
        ge=0.0,
        le=100.0,
        description="Min percentage of samples that must satisfy the bounds.",
    )

    @field_validator("max", "min")
    @classmethod
    def _no_inf(cls, v: float | None) -> float | None:
        return v

    def model_post_init(self, _ctx: Any) -> None:
        if self.max is None and self.min is None:
            raise ValueError(
                f"prometheus probe '{self.name}': at least one of `max` or `min` is required"
            )


class SteadyStateSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    hypothesis: str = Field(
        "",
        description="What should stay true during the experiment. Recorded in the report.",
    )
    http_probes: list[HttpProbeSpec] = Field(default_factory=list)
    prometheus_probes: list[PrometheusProbeSpec] = Field(default_factory=list)


class ObservabilitySpec(BaseModel):
    """Optional in-cluster observability stack (v2)."""

    model_config = ConfigDict(extra="forbid")

    prometheus: bool = Field(
        default=False,
        description="Helm-install Prometheus + kube-state-metrics into the cluster.",
    )
    prometheus_node_port: int = Field(30090, ge=30000, le=32767)


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


_TIER_MAX_DROP = {"critical": 2.0, "standard": 5.0, "best-effort": 15.0}


class GateSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    min_pass_rate_pct: float = Field(100.0, ge=0.0, le=100.0)
    fail_on_probe_breach: bool = True
    require_steady_state_return: bool = Field(
        default=True,
        description=(
            "Fail when the recovery window does not come back to this run's "
            "own baseline, even if both windows sit inside the absolute SLO."
        ),
    )
    halt_when_steady_state_lost: bool = Field(
        default=False,
        description=(
            "Skip experiments that have not started once a during-window probe "
            "has already breached its SLO."
        ),
    )
    tier: Literal["critical", "standard", "best-effort"] = "standard"
    max_score_drop: float | None = Field(
        default=None,
        ge=0.0,
        le=100.0,
        description="Max points the resilience score may fall versus the last green run.",
    )

    def model_post_init(self, _ctx: Any) -> None:
        if self.max_score_drop is None:
            self.max_score_drop = _TIER_MAX_DROP[self.tier]


class ChaosConfig(BaseModel):
    """Top-level chaos.yaml schema."""

    model_config = ConfigDict(extra="forbid")

    cluster: ClusterSpec = Field(default_factory=ClusterSpec)
    observability: ObservabilitySpec = Field(default_factory=ObservabilitySpec)
    target: TargetSpec
    steady_state: SteadyStateSpec = Field(default_factory=SteadyStateSpec)
    experiments: list[ExperimentSpec] = Field(..., min_length=1)
    gate: GateSpec = Field(default_factory=GateSpec)

    def model_post_init(self, _ctx: Any) -> None:
        names = [exp.name for exp in self.experiments]
        if len(names) != len(set(names)):
            raise ValueError(
                "experiment names must be unique; duplicate names overwrite one Kubernetes object"
            )
        for exp in self.experiments:
            exp.validate_engine_fields()
        if self.steady_state.prometheus_probes and not self.observability.prometheus:
            raise ValueError(
                "prometheus probes are configured but observability.prometheus is false"
            )
        _require_dns1123(self.target.namespace, "target.namespace")
        for mapping in self.cluster.port_mappings:
            if not _PORT_MAPPING.fullmatch(mapping):
                raise ValueError(
                    f"cluster.port_mappings entry {mapping!r} is not a k3d port mapping"
                )
        probe_names: list[str] = []
        for probe in self.steady_state.http_probes:
            _require_dns1123(probe.name, "http probe name")
            probe_names.append(probe.name)
        for probe in self.steady_state.prometheus_probes:
            _require_dns1123(probe.name, "prometheus probe name")
            probe_names.append(probe.name)
        if len(probe_names) != len(set(probe_names)):
            raise ValueError(
                "probe names must be unique across HTTP and Prometheus probes; "
                "duplicates overwrite one another in the gate"
            )
        for exp in self.experiments:
            _require_dns1123(exp.name, "experiment name")


def _require_dns1123(value: str, what: str) -> None:
    if not _DNS1123.fullmatch(value) or len(value) > 63:
        raise ValueError(
            f"{what} {value!r} must be a DNS-1123 label: lowercase letters, digits, "
            "and hyphens, starting and ending with a letter or digit, at most 63 characters"
        )


def namespace_mismatches(manifest_text: str, expected: str) -> list[str]:
    """Names of resources whose namespace disagrees with target.namespace.

    Cluster-scoped objects are ignored. A resource with no namespace is left
    alone, because kubectl apply then uses the current context namespace.
    """
    mismatches: list[str] = []
    for doc in yaml.safe_load_all(manifest_text):
        if not isinstance(doc, dict):
            continue
        kind = str(doc.get("kind") or "object")
        if kind in _CLUSTER_SCOPED:
            continue
        meta = doc.get("metadata") if isinstance(doc.get("metadata"), dict) else {}
        namespace = meta.get("namespace")
        if not namespace or namespace == expected:
            continue
        name = meta.get("name") or kind
        mismatches.append(
            f"{kind}/{name} namespace {namespace!r} does not match target.namespace {expected!r}"
        )
    return mismatches


def resolve_manifest(config_path: str | Path, manifest: str) -> Path:
    """Locate the workload manifest.

    Absolute paths are used as given. Relative paths are tried next to the
    chaos.yaml file first, then against the current working directory. The
    nginx example is written from the repo root, so the second candidate is
    what CI uses today.
    """
    path = Path(manifest)
    if path.is_absolute():
        return path
    beside_config = (Path(config_path).parent / path).resolve()
    if beside_config.is_file():
        return beside_config
    from_cwd = (Path.cwd() / path).resolve()
    if from_cwd.is_file():
        return from_cwd
    return beside_config


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
