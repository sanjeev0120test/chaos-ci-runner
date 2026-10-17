"""Compare a run's recovery window with its own baseline.

An absolute SLO can stay green while the service is still far from the
steady state measured before chaos. Recovery has to come back to that
baseline. The band is wide enough that one or two missed samples do not
fail a healthy service, and tight enough that a service left down does.
"""

from __future__ import annotations

from dataclasses import dataclass

from chaos_ci_runner.probes import ProbeWindow

# Percentage points. A 10-sample window can miss one sample and still return.
SUCCESS_DROP_POINTS = 10.0
# Milliseconds and ratio, both required, so a small jitter is not a miss.
P99_INCREASE_MS = 100.0
P99_RATIO = 1.5


@dataclass
class RecoveryCheck:
    probe: str
    kind: str
    metric: str
    baseline: float
    recovery: float
    returned: bool

    def to_dict(self) -> dict:
        return {
            "probe": self.probe,
            "kind": self.kind,
            "metric": self.metric,
            "baseline": round(self.baseline, 3),
            "recovery": round(self.recovery, 3),
            "returned": self.returned,
        }


def _index(windows: list[ProbeWindow]) -> dict[tuple[str, str, str], ProbeWindow]:
    found: dict[tuple[str, str, str], ProbeWindow] = {}
    for window in windows:
        if window.window in ("baseline", "recovery"):
            found[(window.probe, window.kind, window.window)] = window
    return found


def assess_recovery(windows: list[ProbeWindow]) -> list[RecoveryCheck]:
    """Pair baseline and recovery for each probe. Skip probes missing either window."""
    found = _index(windows)
    probes = sorted({(name, kind) for name, kind, _window in found})
    checks: list[RecoveryCheck] = []
    for name, kind in probes:
        baseline = found.get((name, kind, "baseline"))
        recovery = found.get((name, kind, "recovery"))
        if baseline is None or recovery is None:
            continue
        if baseline.total == 0 or recovery.total == 0:
            continue
        success_drop = baseline.success_rate_pct - recovery.success_rate_pct
        checks.append(
            RecoveryCheck(
                probe=name,
                kind=kind,
                metric="success_rate_pct",
                baseline=baseline.success_rate_pct,
                recovery=recovery.success_rate_pct,
                returned=success_drop <= SUCCESS_DROP_POINTS,
            )
        )
        if kind == "http" and baseline.latency_p99_ms > 0 and recovery.latency_p99_ms > 0:
            slower_ms = recovery.latency_p99_ms - baseline.latency_p99_ms
            ratio_hit = recovery.latency_p99_ms >= baseline.latency_p99_ms * P99_RATIO
            checks.append(
                RecoveryCheck(
                    probe=name,
                    kind=kind,
                    metric="latency_p99_ms",
                    baseline=baseline.latency_p99_ms,
                    recovery=recovery.latency_p99_ms,
                    returned=not (slower_ms >= P99_INCREASE_MS and ratio_hit),
                )
            )
    return checks
