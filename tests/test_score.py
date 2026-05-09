"""Tests for the resilience score (v3)."""

from __future__ import annotations

import time

from chaos_ci_runner.engines.base import ExperimentResult
from chaos_ci_runner.gate import GateOutcome, ProbeBreach
from chaos_ci_runner.probes import ProbeSample, ProbeWindow
from chaos_ci_runner.score import compute


def _exp(succ: bool = True) -> ExperimentResult:
    return ExperimentResult(
        name="x",
        engine="chaos-mesh",
        started_at=time.time(),
        finished_at=time.time() + 1,
        status="succeeded" if succ else "failed",
    )


def _win(rate_pct: float, n: int = 100) -> ProbeWindow:
    n_ok = int(round(n * rate_pct / 100))
    samples = [ProbeSample(ok=True, latency_ms=10, status=200) for _ in range(n_ok)]
    samples += [ProbeSample(ok=False, latency_ms=10, status=500) for _ in range(n - n_ok)]
    return ProbeWindow(probe="p", window="baseline", kind="http", samples=samples)


def test_perfect_run_scores_100() -> None:
    s = compute(
        experiments=[_exp(), _exp()],
        windows=[_win(100), _win(100)],
        gate=GateOutcome(
            passed=True,
            pass_rate_pct=100.0,
            experiments_total=2,
            experiments_succeeded=2,
        ),
    )
    assert s.score == 100.0
    assert s.experiment_pass_rate == 1.0
    assert s.probe_success_rate == 1.0


def test_half_experiments_failed_drops_score() -> None:
    s = compute(
        experiments=[_exp(True), _exp(False)],
        windows=[_win(100)],
        gate=GateOutcome(
            passed=False,
            pass_rate_pct=50.0,
            experiments_total=2,
            experiments_succeeded=1,
        ),
    )
    # 60 * 0.5 + 25 * 1.0 + 15 * 1.0 = 70
    assert 65 <= s.score <= 75


def test_breaches_apply_penalty() -> None:
    breaches = [
        ProbeBreach(probe="p", window="during", metric="x", threshold=99, actual=50)
        for _ in range(5)
    ]
    s = compute(
        experiments=[_exp()],
        windows=[_win(100)],
        gate=GateOutcome(
            passed=False,
            pass_rate_pct=100.0,
            experiments_total=1,
            experiments_succeeded=1,
            breaches=breaches,
        ),
    )
    # All 15 breach points zeroed (5 breaches >= cap)
    assert 84 <= s.score <= 86


def test_no_probes_does_not_penalize() -> None:
    s = compute(
        experiments=[_exp()],
        windows=[],
        gate=GateOutcome(
            passed=True,
            pass_rate_pct=100.0,
            experiments_total=1,
            experiments_succeeded=1,
        ),
    )
    # 60 + 25 + 15
    assert s.score == 100.0
