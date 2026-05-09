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


@dataclass
class RunReport:
    config_path: str
    cluster_name: str
    started_at: float
    finished_at: float
    experiments: list[ExperimentResult]
    probe_windows: list[ProbeWindow]
    gate: GateOutcome

    @property
    def duration_s(self) -> float:
        return max(0.0, self.finished_at - self.started_at)

    def to_dict(self) -> dict:
        return {
            "version": __version__,
            "config": self.config_path,
            "cluster": self.cluster_name,
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


def render_markdown(report: RunReport) -> str:
    g = report.gate
    verdict = "PASS" if g.passed else "FAIL"
    lines: list[str] = []
    lines.append(f"## chaos-ci-runner: {verdict}")
    lines.append("")
    lines.append(
        f"- experiments: **{g.experiments_succeeded}/{g.experiments_total}** "
        f"({g.pass_rate_pct:.1f}% pass rate)"
    )
    lines.append(f"- probe breaches: **{len(g.breaches)}**")
    lines.append(f"- run duration: {report.duration_s:.1f}s")
    if g.reasons:
        lines.append("")
        lines.append("**Reasons:**")
        for r in g.reasons:
            lines.append(f"- {r}")

    if report.experiments:
        lines.append("")
        lines.append("### Experiments")
        lines.append("")
        lines.append("| name | engine | status | duration |")
        lines.append("|---|---|---|---|")
        for e in report.experiments:
            lines.append(f"| `{e.name}` | {e.engine} | {e.status} | {e.duration_s:.1f}s |")

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
                f"| `{w.probe}` | {w.window} | {w.success_rate_pct:.1f} | "
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
                f"| `{w.probe}` | {w.window} | {w.success_rate_pct:.1f} | "
                f"{w.value_min:g} | {w.value_avg:g} | {w.value_max:g} | {w.total} |"
            )

    if g.breaches:
        lines.append("")
        lines.append("### Probe breaches")
        lines.append("")
        lines.append("| probe | window | metric | threshold | actual |")
        lines.append("|---|---|---|---|---|")
        for b in g.breaches:
            lines.append(f"| `{b.probe}` | {b.window} | {b.metric} | {b.threshold} | {b.actual} |")

    lines.append("")
    lines.append(f"_chaos-ci-runner v{__version__}_")
    return "\n".join(lines) + "\n"
