"""CLI smoke tests: confirm subcommands are registered and `doctor` works.

We only assert that the commands are wired into the Typer app and that
`doctor` runs to completion (its exit code depends on whether CI has
kubectl/helm/k3d/docker on PATH; we accept either 0 or 2).
"""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from chaos_ci_runner.cli import app


def test_subcommands_registered() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    out = result.stdout
    for cmd in ("version", "doctor", "validate", "schema", "plan", "run", "regression"):
        assert cmd in out, f"missing subcommand in --help: {cmd}\n{out}"


def test_version_runs() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert result.stdout.strip()  # non-empty


def test_plan_prints_example_experiments() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    runner = CliRunner()
    result = runner.invoke(
        app,
        ["plan", "--config", str(repo_root / "examples" / "nginx" / "chaos.yaml")],
    )
    assert result.exit_code == 0, result.output
    assert "PodChaos" in result.output
    assert "NetworkChaos" in result.output


def test_schema_includes_gate_policy() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["schema"])
    assert result.exit_code == 0
    assert "max_score_drop" in result.stdout
    assert "hypothesis" in result.stdout


def test_regression_uses_tier_from_config(tmp_path) -> None:
    current = tmp_path / "current.json"
    baseline = tmp_path / "baseline.json"
    current.write_text('{"score": {"score": 96}}', encoding="utf-8")
    baseline.write_text('{"score": {"score": 100}}', encoding="utf-8")
    cfg = tmp_path / "chaos.yaml"
    cfg.write_text(
        "target: {manifest: m.yaml}\n"
        "experiments:\n"
        "  - {name: kill, engine: chaos-mesh, spec: {action: pod-kill, mode: one}}\n"
        "gate: {tier: critical}\n",
        encoding="utf-8",
    )
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "regression",
            "--current",
            str(current),
            "--baseline",
            str(baseline),
            "--config",
            str(cfg),
        ],
    )
    assert result.exit_code == 1, result.stdout


def test_doctor_runs_and_reports() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["doctor"])
    # 0 = all tools present, 2 = at least one missing/unhealthy. Both
    # are valid outcomes for a unit test depending on the runner.
    assert result.exit_code in (0, 2), result.stdout
    assert "doctor:" in result.stdout
