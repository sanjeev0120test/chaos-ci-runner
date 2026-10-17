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
from chaos_ci_runner.steady_state import RecoveryCheck, assess_recovery


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
    recovery: list[RecoveryCheck] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "passed": self.passed,
            "pass_rate_pct": round(self.pass_rate_pct, 3),
            "experiments_total": self.experiments_total,
            "experiments_succeeded": self.experiments_succeeded,
            "breaches": [b.to_dict() for b in self.breaches],
            "reasons": self.reasons,
            "recovery": [c.to_dict() for c in self.recovery],
        }


def during_steady_state_lost(
    *,
    http_probe_specs: list[HttpProbeSpec],
    prometheus_probe_specs: list[PrometheusProbeSpec],
    windows: list[ProbeWindow],
) -> str | None:
    """Reason to stop injecting, once a during-window has samples and has breached.

    An empty during window is not a loss: the probe may not have sampled yet.
    """
    during = [w for w in windows if w.window == "during" and w.total > 0]
    if not during:
        return None
    outcome = evaluate(
        gate=GateSpec(
            min_pass_rate_pct=0,
            fail_on_probe_breach=True,
            require_steady_state_return=False,
        ),
        experiments=[
            ExperimentResult(
                name="live",
                engine="chaos-mesh",
                started_at=0,
                finished_at=0,
                status="succeeded",
            )
        ],
        http_probe_specs=http_probe_specs,
        prometheus_probe_specs=prometheus_probe_specs,
        windows=during,
    )
    if outcome.passed:
        return None
    return "; ".join(outcome.reasons) or "during-window probe breached its SLO"


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
       rate against PromQL bounds) is a breach. A window that collected
       no samples is a breach too: the probe did not observe steady state.
    """
    total = len(experiments)
    succeeded = sum(1 for e in experiments if e.succeeded)
    rate = 100.0 * succeeded / total if total else 0.0

    breaches: list[ProbeBreach] = []
    http_specs = {p.name: p for p in http_probe_specs}
    prom_specs = {p.name: p for p in prometheus_probe_specs}

    for w in windows:
        if w.kind == "http":
            spec = http_specs.get(w.probe)
            if spec is None:
                continue
            if w.total == 0:
                breaches.append(
                    ProbeBreach(
                        probe=w.probe,
                        window=w.window,
                        metric="samples",
                        threshold=1,
                        actual=0,
                    )
                )
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
            if w.total == 0:
                breaches.append(
                    ProbeBreach(
                        probe=w.probe,
                        window=w.window,
                        metric="samples",
                        threshold=1,
                        actual=0,
                    )
                )
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

    recovery = assess_recovery(windows)
    if gate.require_steady_state_return:
        for check in recovery:
            if check.returned:
                continue
            breaches.append(
                ProbeBreach(
                    probe=check.probe,
                    window="recovery",
                    metric=f"recovery_{check.metric}",
                    threshold=check.baseline,
                    actual=check.recovery,
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
    missed = [c for c in recovery if not c.returned]
    if gate.require_steady_state_return and missed:
        passed = False
        for check in missed:
            reasons.append(
                f"steady state did not return: {check.probe} {check.metric} "
                f"{check.baseline:.1f} -> {check.recovery:.1f}"
            )

    return GateOutcome(
        passed=passed,
        pass_rate_pct=rate,
        experiments_total=total,
        experiments_succeeded=succeeded,
        breaches=breaches,
        reasons=reasons,
        recovery=recovery,
    )
