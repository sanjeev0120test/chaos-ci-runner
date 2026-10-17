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

    @property
    def exit_code(self) -> int:
        if self.status == "regression":
            return 1
        if self.status == "missing_current":
            return 2
        return 0


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
    cur_score = (current.get("score") or {}).get("score")
    base_score = (baseline.get("score") or {}).get("score")
    if cur_score is None:
        return ScoreDiff(
            status="missing_current",
            current=None,
            baseline=float(base_score) if base_score is not None else None,
            drop=None,
            max_drop=max_drop,
        )
    if base_score is None:
        return ScoreDiff(
            status="baseline_unscored",
            current=float(cur_score),
            baseline=None,
            drop=None,
            max_drop=max_drop,
        )
    drop = float(base_score) - float(cur_score)
    status: Status = "regression" if drop > max_drop else "ok"
    return ScoreDiff(
        status=status,
        current=float(cur_score),
        baseline=float(base_score),
        drop=drop,
        max_drop=max_drop,
    )
