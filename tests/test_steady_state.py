"""Recovery has to come back to the baseline measured in the same run."""

from __future__ import annotations

from chaos_ci_runner.config import GateSpec, HttpProbeSpec
from chaos_ci_runner.engines.base import ExperimentResult
from chaos_ci_runner.gate import evaluate
from chaos_ci_runner.probes import ProbeSample, ProbeWindow
from chaos_ci_runner.report import RunReport, render_markdown
from chaos_ci_runner.steady_state import assess_recovery


def _http(window: str, *, ok: int, fail: int, latency_ms: float) -> ProbeWindow:
    samples = [ProbeSample(ok=True, latency_ms=latency_ms, status=200) for _ in range(ok)]
    samples += [ProbeSample(ok=False, latency_ms=latency_ms, status=500) for _ in range(fail)]
    return ProbeWindow(probe="api", window=window, kind="http", samples=samples)  # type: ignore[arg-type]


def _prom(window: str, *, ok: int, fail: int) -> ProbeWindow:
    samples = [ProbeSample(ok=True, latency_ms=0, status=200) for _ in range(ok)]
    samples += [
        ProbeSample(ok=False, latency_ms=0, status=0, error="no-value") for _ in range(fail)
    ]
    return ProbeWindow(probe="ready", window=window, kind="prometheus", samples=samples)  # type: ignore[arg-type]


def test_recovery_inside_the_band_counts_as_returned() -> None:
    checks = assess_recovery(
        [
            _http("baseline", ok=100, fail=0, latency_ms=40),
            _http("recovery", ok=95, fail=5, latency_ms=50),
        ]
    )
    assert checks
    assert all(c.returned for c in checks)


def test_success_that_stays_down_does_not_count_as_returned() -> None:
    checks = assess_recovery(
        [
            _http("baseline", ok=100, fail=0, latency_ms=40),
            _http("recovery", ok=70, fail=30, latency_ms=40),
        ]
    )
    success = next(c for c in checks if c.metric == "success_rate_pct")
    assert success.returned is False
    assert success.baseline == 100
    assert success.recovery == 70


def test_p99_that_stays_high_does_not_count_as_returned() -> None:
    checks = assess_recovery(
        [
            _http("baseline", ok=100, fail=0, latency_ms=40),
            _http("recovery", ok=100, fail=0, latency_ms=200),
        ]
    )
    latency = next(c for c in checks if c.metric == "latency_p99_ms")
    assert latency.returned is False


def test_small_p99_jitter_still_counts_as_returned() -> None:
    checks = assess_recovery(
        [
            _http("baseline", ok=100, fail=0, latency_ms=40),
            _http("recovery", ok=100, fail=0, latency_ms=80),
        ]
    )
    latency = next(c for c in checks if c.metric == "latency_p99_ms")
    assert latency.returned is True


def test_prometheus_success_must_come_back() -> None:
    checks = assess_recovery(
        [
            _prom("baseline", ok=10, fail=0),
            _prom("recovery", ok=7, fail=3),
        ]
    )
    assert len(checks) == 1
    assert checks[0].returned is False


def test_a_single_window_is_not_compared() -> None:
    assert assess_recovery([_http("baseline", ok=10, fail=0, latency_ms=20)]) == []


def test_gate_fails_when_recovery_misses_its_own_baseline() -> None:
    spec = HttpProbeSpec(name="api", url="http://x", success_rate_pct=50, latency_p99_ms=1000)
    out = evaluate(
        gate=GateSpec(),
        experiments=[
            ExperimentResult(
                name="x",
                engine="chaos-mesh",
                started_at=0,
                finished_at=1,
                status="succeeded",
            )
        ],
        http_probe_specs=[spec],
        prometheus_probe_specs=[],
        windows=[
            _http("baseline", ok=100, fail=0, latency_ms=40),
            _http("during", ok=100, fail=0, latency_ms=40),
            _http("recovery", ok=70, fail=30, latency_ms=40),
        ],
    )
    assert out.passed is False
    assert any("steady state did not return" in r for r in out.reasons)
    assert any(b.metric == "recovery_success_rate_pct" for b in out.breaches)


def test_gate_can_record_the_miss_without_failing() -> None:
    spec = HttpProbeSpec(name="api", url="http://x", success_rate_pct=50, latency_p99_ms=1000)
    out = evaluate(
        gate=GateSpec(require_steady_state_return=False),
        experiments=[
            ExperimentResult(
                name="x",
                engine="chaos-mesh",
                started_at=0,
                finished_at=1,
                status="succeeded",
            )
        ],
        http_probe_specs=[spec],
        prometheus_probe_specs=[],
        windows=[
            _http("baseline", ok=100, fail=0, latency_ms=40),
            _http("recovery", ok=70, fail=30, latency_ms=40),
        ],
    )
    assert out.passed is True
    assert any(not c.returned for c in out.recovery)


def test_report_names_a_recovery_that_came_back() -> None:
    spec_report = RunReport(
        config_path="chaos.yaml",
        cluster_name="c",
        started_at=0,
        finished_at=1,
        experiments=[],
        probe_windows=[],
        gate=evaluate(
            gate=GateSpec(),
            experiments=[],
            http_probe_specs=[],
            prometheus_probe_specs=[],
            windows=[
                _http("baseline", ok=100, fail=0, latency_ms=40),
                _http("recovery", ok=100, fail=0, latency_ms=40),
            ],
        ),
    )
    md = render_markdown(spec_report)
    assert "Steady state returned" in md
    assert "returned" in md
