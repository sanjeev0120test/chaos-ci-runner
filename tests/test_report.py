"""Tests for report rendering."""

from __future__ import annotations

import json
import time
from pathlib import Path

from chaos_ci_runner.engines.base import ExperimentResult
from chaos_ci_runner.gate import GateOutcome, ProbeBreach
from chaos_ci_runner.probes import ProbeSample, ProbeWindow
from chaos_ci_runner.report import RunReport, render_markdown, write_json, write_markdown


def _report(passed: bool = True) -> RunReport:
    now = time.time()
    return RunReport(
        config_path="chaos.yaml",
        cluster_name="ccr-test",
        started_at=now,
        finished_at=now + 30,
        experiments=[
            ExperimentResult(
                name="kill",
                engine="chaos-mesh",
                started_at=now,
                finished_at=now + 10,
                status="succeeded",
            ),
        ],
        probe_windows=[
            ProbeWindow(
                probe="root",
                window="baseline",
                samples=[ProbeSample(ok=True, latency_ms=50, status=200)] * 5,
            ),
        ],
        gate=GateOutcome(
            passed=passed,
            pass_rate_pct=100.0 if passed else 50.0,
            experiments_total=1,
            experiments_succeeded=1 if passed else 0,
            breaches=[]
            if passed
            else [
                ProbeBreach(
                    probe="root",
                    window="during",
                    metric="success_rate_pct",
                    threshold=99.0,
                    actual=80.0,
                )
            ],
            reasons=[] if passed else ["mock breach"],
        ),
    )


def test_render_markdown_pass_includes_pass_verdict() -> None:
    md = render_markdown(_report(passed=True))
    assert "PASS" in md
    assert "kill" in md
    assert "succeeded" in md


def test_render_markdown_fail_includes_breaches_section() -> None:
    md = render_markdown(_report(passed=False))
    assert "FAIL" in md
    assert "Probe breaches" in md
    assert "mock breach" in md


def test_write_json_and_markdown(tmp_path: Path) -> None:
    rpt = _report()
    j = write_json(rpt, tmp_path / "out" / "report.json")
    m = write_markdown(rpt, tmp_path / "out" / "report.md")
    assert j.is_file() and m.is_file()
    data = json.loads(j.read_text(encoding="utf-8"))
    assert data["gate"]["passed"] is True
    assert data["experiments"][0]["name"] == "kill"
