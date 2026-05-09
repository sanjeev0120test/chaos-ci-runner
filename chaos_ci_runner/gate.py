"""SLO gate evaluation.

Given a list of experiment results and the probe windows captured around
each one, decide pass/fail and produce a structured `GateOutcome` that
the report module renders.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from chaos_ci_runner.config import GateSpec, HttpProbeSpec, PrometheusProbeSpec
from chaos_ci_runner.engines.base import ExperimentResult
from chaos_ci_runner.probes import ProbeWindow


@dataclass
class ProbeBreach:
    probe: str
    window: str
    metric: str
    threshold: float
    actual: float

    def to_dict(self) -> dict:
        return {
            "probe": self.probe,
            "window": self.window,
            "metric": self.metric,
            "threshold": self.threshold,
            "actual": round(self.actual, 3),
        }


@dataclass
class GateOutcome:
    passed: bool
    pass_rate_pct: float
    experiments_total: int
    experiments_succeeded: int
    breaches: list[ProbeBreach] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "passed": self.passed,
            "pass_rate_pct": round(self.pass_rate_pct, 3),
            "experiments_total": self.experiments_total,
            "experiments_succeeded": self.experiments_succeeded,
            "breaches": [b.to_dict() for b in self.breaches],
            "reasons": self.reasons,
        }


def evaluate(
    *,
    gate: GateSpec,
    experiments: list[ExperimentResult],
    http_probe_specs: list[HttpProbeSpec],
    prometheus_probe_specs: list[PrometheusProbeSpec],
    windows: list[ProbeWindow],
) -> GateOutcome:
    """Evaluate gate. Two independent checks:

    1. Experiment success rate must meet `gate.min_pass_rate_pct`.
    2. If `gate.fail_on_probe_breach`, any probe window that breaches
       its SLO (HTTP success rate / p99 latency, or Prometheus success
       rate against PromQL bounds) is a breach.
    """
    total = len(experiments)
    succeeded = sum(1 for e in experiments if e.succeeded)
    rate = 100.0 * succeeded / total if total else 0.0

    breaches: list[ProbeBreach] = []
    http_specs = {p.name: p for p in http_probe_specs}
    prom_specs = {p.name: p for p in prometheus_probe_specs}

    for w in windows:
        if w.total == 0:
            continue
        if w.kind == "http":
            spec = http_specs.get(w.probe)
            if spec is None:
                continue
            if w.success_rate_pct < spec.success_rate_pct:
                breaches.append(
                    ProbeBreach(
                        probe=w.probe,
                        window=w.window,
                        metric="success_rate_pct",
                        threshold=spec.success_rate_pct,
                        actual=w.success_rate_pct,
                    )
                )
            if w.latency_p99_ms > spec.latency_p99_ms:
                breaches.append(
                    ProbeBreach(
                        probe=w.probe,
                        window=w.window,
                        metric="latency_p99_ms",
                        threshold=float(spec.latency_p99_ms),
                        actual=w.latency_p99_ms,
                    )
                )
        elif w.kind == "prometheus":
            pspec = prom_specs.get(w.probe)
            if pspec is None:
                continue
            if w.success_rate_pct < pspec.success_rate_pct:
                breaches.append(
                    ProbeBreach(
                        probe=w.probe,
                        window=w.window,
                        metric="prom_success_rate_pct",
                        threshold=pspec.success_rate_pct,
                        actual=w.success_rate_pct,
                    )
                )

    reasons: list[str] = []
    passed = True
    if rate < gate.min_pass_rate_pct:
        passed = False
        reasons.append(
            f"experiment pass rate {rate:.1f}% below required {gate.min_pass_rate_pct:.1f}%"
        )
    if gate.fail_on_probe_breach and breaches:
        passed = False
        reasons.append(f"{len(breaches)} probe SLO breach(es)")

    return GateOutcome(
        passed=passed,
        pass_rate_pct=rate,
        experiments_total=total,
        experiments_succeeded=succeeded,
        breaches=breaches,
        reasons=reasons,
    )
