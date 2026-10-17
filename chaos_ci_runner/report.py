"""Render structured runs into JSON and a Markdown PR comment.

The Markdown is tuned for posting on a GitHub PR or as a step summary.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from chaos_ci_runner import __version__
from chaos_ci_runner.engines.base import ExperimentResult
from chaos_ci_runner.gate import GateOutcome
from chaos_ci_runner.probes import ProbeWindow
from chaos_ci_runner.score import ResilienceScore


@dataclass
class RunReport:
    config_path: str
    cluster_name: str
    started_at: float
    finished_at: float
    experiments: list[ExperimentResult]
    probe_windows: list[ProbeWindow]
    gate: GateOutcome
    score: ResilienceScore | None = None
    commit_sha: str | None = None
    hypothesis: str = ""
    tier: str = "standard"
    max_score_drop: float = 5.0

    @property
    def duration_s(self) -> float:
        return max(0.0, self.finished_at - self.started_at)

    def to_dict(self) -> dict:
        return {
            "version": __version__,
            "config": self.config_path,
            "cluster": self.cluster_name,
            "commit_sha": self.commit_sha,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "duration_s": round(self.duration_s, 2),
            "experiments": [
                {
                    "name": e.name,
                    "engine": e.engine,
                    "status": e.status,
                    "duration_s": round(e.duration_s, 2),
                    "message": e.message,
                }
                for e in self.experiments
            ],
            "probes": [w.to_dict() for w in self.probe_windows],
            "gate": self.gate.to_dict(),
            "score": self.score.to_dict() if self.score else None,
            "hypothesis": self.hypothesis,
            "policy": {
                "tier": self.tier,
                "max_score_drop": self.max_score_drop,
            },
        }


def write_json(report: RunReport, path: str | Path) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(report.to_dict(), indent=2), encoding="utf-8")
    return p


def write_markdown(report: RunReport, path: str | Path) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(render_markdown(report), encoding="utf-8")
    return p


def md_cell(value: object) -> str:
    """Keep a table cell on one line when a name contains a pipe or newline."""
    return str(value).replace("\\", "\\\\").replace("|", "\\|").replace("\n", " ")


def cap_markdown(text: str, limit: int = 65536) -> str:
    """Keep a GitHub step summary and pull-request comment inside a size budget."""
    raw = text.encode("utf-8")
    if len(raw) <= limit:
        return text
    note = "\n\n_report truncated_\n"
    budget = max(0, limit - len(note.encode("utf-8")))
    clipped = raw[:budget].decode("utf-8", errors="ignore")
    return clipped + note


def render_markdown(report: RunReport) -> str:
    g = report.gate
    verdict = "PASS" if g.passed else "FAIL"
    lines: list[str] = []
    score_suffix = f" (score {report.score.score:.1f}/100)" if report.score else ""
    lines.append(f"## chaos-ci-runner: {verdict}{score_suffix}")
    lines.append("")
    lines.append(f"- tier: `{report.tier}` (max score drop {report.max_score_drop:g})")
    if report.hypothesis.strip():
        lines.append(f"- hypothesis: {report.hypothesis.strip()}")
    lines.append(
        f"- experiments: **{g.experiments_succeeded}/{g.experiments_total}** "
        f"({g.pass_rate_pct:.1f}% pass rate)"
    )
    lines.append(f"- probe breaches: **{len(g.breaches)}**")
    lines.append(f"- run duration: {report.duration_s:.1f}s")
    if report.score is not None:
        lines.append(
            f"- score breakdown: experiments {report.score.breakdown['experiments']:.1f} "
            f"+ probes {report.score.breakdown['probes']:.1f} "
            f"+ breach penalty {report.score.breakdown['breach_penalty']:.1f}"
        )
    if g.reasons:
        lines.append("")
        lines.append("**Reasons:**")
        for r in g.reasons:
            lines.append(f"- {r}")

    if g.recovery:
        lines.append("")
        lines.append("### Steady state returned")
        lines.append("")
        lines.append("| probe | metric | baseline | recovery | |")
        lines.append("|---|---|---|---|---|")
        for check in g.recovery:
            mark = "returned" if check.returned else "still off"
            lines.append(
                f"| `{md_cell(check.probe)}` | {md_cell(check.metric)} | {check.baseline:.1f} | "
                f"{check.recovery:.1f} | {mark} |"
            )

    if report.experiments:
        lines.append("")
        lines.append("### Experiments")
        lines.append("")
        lines.append("| name | engine | status | duration |")
        lines.append("|---|---|---|---|")
        for e in report.experiments:
            lines.append(
                f"| `{md_cell(e.name)}` | {md_cell(e.engine)} | {md_cell(e.status)} | "
                f"{e.duration_s:.1f}s |"
            )

    http_windows = [w for w in report.probe_windows if w.kind == "http"]
    prom_windows = [w for w in report.probe_windows if w.kind == "prometheus"]

    if http_windows:
        lines.append("")
        lines.append("### HTTP probes")
        lines.append("")
        lines.append("| probe | window | success% | p99 (ms) | samples |")
        lines.append("|---|---|---|---|---|")
        for w in http_windows:
            lines.append(
                f"| `{md_cell(w.probe)}` | {w.window} | {w.success_rate_pct:.1f} | "
                f"{w.latency_p99_ms:.1f} | {w.total} |"
            )

    if prom_windows:
        lines.append("")
        lines.append("### Prometheus probes")
        lines.append("")
        lines.append("| probe | window | success% | min | avg | max | samples |")
        lines.append("|---|---|---|---|---|---|---|")
        for w in prom_windows:
            lines.append(
                f"| `{md_cell(w.probe)}` | {w.window} | {w.success_rate_pct:.1f} | "
                f"{w.value_min:g} | {w.value_avg:g} | {w.value_max:g} | {w.total} |"
            )

    if g.breaches:
        lines.append("")
        lines.append("### Probe breaches")
        lines.append("")
        lines.append("| probe | window | metric | threshold | actual |")
        lines.append("|---|---|---|---|---|")
        for b in g.breaches:
            lines.append(
                f"| `{md_cell(b.probe)}` | {b.window} | {md_cell(b.metric)} | "
                f"{b.threshold} | {b.actual} |"
            )

    lines.append("")
    lines.append(f"_chaos-ci-runner v{__version__}_")
    return cap_markdown("\n".join(lines) + "\n")
