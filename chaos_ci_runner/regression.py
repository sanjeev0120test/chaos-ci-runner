"""Diff a run's resilience score against a previous report."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

Status = Literal["ok", "regression", "missing_current", "baseline_unscored"]


@dataclass
class ScoreDiff:
    status: Status
    current: float | None
    baseline: float | None
    drop: float | None
    max_drop: float
    changes: list[str]

    @property
    def exit_code(self) -> int:
        if self.status == "regression":
            return 1
        if self.status == "missing_current":
            return 2
        return 0


def _as_float(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def explain_changes(current: dict[str, Any], baseline: dict[str, Any]) -> list[str]:
    """Name the experiments and probes that moved between two reports.

    The score is still the gate. These lines are what a reviewer reads
    instead of diffing two JSON artifacts by hand.
    """
    lines: list[str] = []
    current_experiments = {item.get("name"): item for item in current.get("experiments") or []}
    baseline_experiments = {item.get("name"): item for item in baseline.get("experiments") or []}
    for name in sorted(set(current_experiments) | set(baseline_experiments), key=lambda n: str(n)):
        cur = current_experiments.get(name)
        base = baseline_experiments.get(name)
        if cur is None and base is not None:
            lines.append(f"experiment `{name}` removed (was {base.get('status')})")
        elif base is None and cur is not None:
            lines.append(f"experiment `{name}` added ({cur.get('status')})")
        elif cur is not None and base is not None and cur.get("status") != base.get("status"):
            lines.append(f"experiment `{name}` {base.get('status')} -> {cur.get('status')}")

    def _probes(report: dict[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
        found: dict[tuple[str, str], dict[str, Any]] = {}
        for probe in report.get("probes") or []:
            found[(str(probe.get("probe")), str(probe.get("window")))] = probe
        return found

    current_probes = _probes(current)
    baseline_probes = _probes(baseline)
    for key in sorted(set(current_probes) | set(baseline_probes)):
        cur = current_probes.get(key)
        base = baseline_probes.get(key)
        label = f"{key[0]}:{key[1]}"
        if cur is None or base is None:
            continue
        cur_rate = _as_float(cur.get("success_rate_pct"))
        base_rate = _as_float(base.get("success_rate_pct"))
        if cur_rate is not None and base_rate is not None and base_rate - cur_rate >= 5:
            lines.append(f"probe `{label}` success {base_rate:.1f}% -> {cur_rate:.1f}%")
        cur_p99 = _as_float(cur.get("latency_p99_ms"))
        base_p99 = _as_float(base.get("latency_p99_ms"))
        if (
            cur_p99 is not None
            and base_p99 is not None
            and cur_p99 - base_p99 >= 50
            and (base_p99 <= 0 or cur_p99 >= base_p99 * 1.2)
        ):
            lines.append(f"probe `{label}` p99 {base_p99:.0f}ms -> {cur_p99:.0f}ms")
    return lines


def compare_reports(
    current: dict[str, Any],
    baseline: dict[str, Any],
    *,
    max_drop: float,
) -> ScoreDiff:
    """Return how far `current` fell below `baseline`.

    A missing current score is an error. A baseline from before scores
    existed is accepted so the first v3 run is not a false regression.
    """
    cur_score = _as_float((current.get("score") or {}).get("score"))
    base_score = _as_float((baseline.get("score") or {}).get("score"))
    changes = explain_changes(current, baseline)
    if cur_score is None:
        return ScoreDiff(
            status="missing_current",
            current=None,
            baseline=base_score,
            drop=None,
            max_drop=max_drop,
            changes=changes,
        )
    if base_score is None:
        return ScoreDiff(
            status="baseline_unscored",
            current=cur_score,
            baseline=None,
            drop=None,
            max_drop=max_drop,
            changes=changes,
        )
    drop = base_score - cur_score
    status: Status = "regression" if drop > max_drop else "ok"
    return ScoreDiff(
        status=status,
        current=cur_score,
        baseline=base_score,
        drop=drop,
        max_drop=max_drop,
        changes=changes,
    )
