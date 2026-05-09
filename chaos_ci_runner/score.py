"""Composite resilience score (v3).

A single number 0-100 per chaos run so teams can track resilience the
same way they track code coverage. The score is deliberately simple:

    score = 60 * experiment_pass_rate
          + 25 * probe_success_rate
          + 15 * (1 - min(1, breaches / 5))

- experiment_pass_rate: fraction of experiments that succeeded
- probe_success_rate: average success rate across all probe windows
- breaches: total probe SLO breaches (capped at 5 in the penalty)

The weights are intentional and documented in docs/roadmap.md so
teams can disagree without fighting the implementation.
"""

from __future__ import annotations

from dataclasses import dataclass

from chaos_ci_runner.engines.base import ExperimentResult
from chaos_ci_runner.gate import GateOutcome
from chaos_ci_runner.probes import ProbeWindow

WEIGHT_EXPERIMENTS = 60.0
WEIGHT_PROBES = 25.0
WEIGHT_BREACHES = 15.0
BREACH_CAP = 5


@dataclass
class ResilienceScore:
    score: float
    experiment_pass_rate: float
    probe_success_rate: float
    breach_count: int
    breakdown: dict[str, float]

    def to_dict(self) -> dict:
        return {
            "score": round(self.score, 2),
            "experiment_pass_rate": round(self.experiment_pass_rate, 4),
            "probe_success_rate": round(self.probe_success_rate, 4),
            "breach_count": self.breach_count,
            "breakdown": {k: round(v, 2) for k, v in self.breakdown.items()},
        }


def compute(
    *,
    experiments: list[ExperimentResult],
    windows: list[ProbeWindow],
    gate: GateOutcome,
) -> ResilienceScore:
    exp_rate = sum(1 for e in experiments if e.succeeded) / len(experiments) if experiments else 0.0

    valid_windows = [w for w in windows if w.total > 0]
    probe_rate = (
        sum(w.success_rate_pct for w in valid_windows) / (100.0 * len(valid_windows))
        if valid_windows
        else 1.0
    )

    breach_count = len(gate.breaches)
    breach_factor = 1.0 - min(1.0, breach_count / BREACH_CAP)

    pts_exp = WEIGHT_EXPERIMENTS * exp_rate
    pts_probe = WEIGHT_PROBES * probe_rate
    pts_breach = WEIGHT_BREACHES * breach_factor
    total = pts_exp + pts_probe + pts_breach

    return ResilienceScore(
        score=total,
        experiment_pass_rate=exp_rate,
        probe_success_rate=probe_rate,
        breach_count=breach_count,
        breakdown={
            "experiments": pts_exp,
            "probes": pts_probe,
            "breach_penalty": pts_breach,
        },
    )
