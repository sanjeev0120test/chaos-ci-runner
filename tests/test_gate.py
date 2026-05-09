"""Tests for SLO gate evaluation."""

from __future__ import annotations

import time

from chaos_ci_runner.config import GateSpec, HttpProbeSpec
from chaos_ci_runner.engines.base import ExperimentResult
from chaos_ci_runner.gate import evaluate
from chaos_ci_runner.probes import ProbeSample, ProbeWindow


def _exp(status: str = "succeeded") -> ExperimentResult:
    now = time.time()
    return ExperimentResult(
        name="x",
        engine="chaos-mesh",
        started_at=now,
        finished_at=now + 5,
        status=status,
    )


def _window(
    name: str,
    win: str,
    *,
    n_ok: int,
    n_fail: int,
    latency_ms: float = 50.0,
) -> ProbeWindow:
    samples = [ProbeSample(ok=True, latency_ms=latency_ms, status=200) for _ in range(n_ok)]
    samples += [ProbeSample(ok=False, latency_ms=latency_ms, status=500) for _ in range(n_fail)]
    return ProbeWindow(probe=name, window=win, samples=samples)  # type: ignore[arg-type]


def test_pass_when_everything_clean() -> None:
    spec = HttpProbeSpec(name="p", url="http://x", success_rate_pct=99, latency_p99_ms=200)
    out = evaluate(
        gate=GateSpec(),
        experiments=[_exp(), _exp()],
        probe_specs=[spec],
        windows=[_window("p", "baseline", n_ok=100, n_fail=0)],
    )
    assert out.passed is True
    assert out.pass_rate_pct == 100.0
    assert out.breaches == []


def test_fail_when_experiment_fails() -> None:
    out = evaluate(
        gate=GateSpec(min_pass_rate_pct=100),
        experiments=[_exp(), _exp(status="failed")],
        probe_specs=[],
        windows=[],
    )
    assert out.passed is False
    assert out.pass_rate_pct == 50.0
    assert any("pass rate" in r for r in out.reasons)


def test_fail_when_probe_breaches_success_rate() -> None:
    spec = HttpProbeSpec(name="p", url="http://x", success_rate_pct=99, latency_p99_ms=200)
    out = evaluate(
        gate=GateSpec(),
        experiments=[_exp()],
        probe_specs=[spec],
        windows=[_window("p", "during", n_ok=80, n_fail=20)],
    )
    assert out.passed is False
    assert any(b.metric == "success_rate_pct" for b in out.breaches)


def test_fail_when_probe_breaches_latency() -> None:
    spec = HttpProbeSpec(name="p", url="http://x", success_rate_pct=50, latency_p99_ms=10)
    out = evaluate(
        gate=GateSpec(),
        experiments=[_exp()],
        probe_specs=[spec],
        windows=[_window("p", "during", n_ok=100, n_fail=0, latency_ms=500.0)],
    )
    assert out.passed is False
    assert any(b.metric == "latency_p99_ms" for b in out.breaches)


def test_breach_ignored_when_disabled() -> None:
    spec = HttpProbeSpec(name="p", url="http://x", success_rate_pct=99, latency_p99_ms=200)
    out = evaluate(
        gate=GateSpec(fail_on_probe_breach=False),
        experiments=[_exp()],
        probe_specs=[spec],
        windows=[_window("p", "during", n_ok=80, n_fail=20)],
    )
    assert out.passed is True
    assert len(out.breaches) >= 1
