"""CLI smoke tests: confirm subcommands are registered and `doctor` works.

We only assert that the commands are wired into the Typer app and that
`doctor` runs to completion (its exit code depends on whether CI has
kubectl/helm/k3d/docker on PATH; we accept either 0 or 2).
"""

from __future__ import annotations

from typer.testing import CliRunner

from chaos_ci_runner.cli import app


def test_subcommands_registered() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    out = result.stdout
    for cmd in ("version", "doctor", "validate", "run", "regression"):
        assert cmd in out, f"missing subcommand in --help: {cmd}\n{out}"


def test_version_runs() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert result.stdout.strip()  # non-empty


def test_doctor_runs_and_reports() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["doctor"])
    # 0 = all tools present, 2 = at least one missing/unhealthy. Both
    # are valid outcomes for a unit test depending on the runner.
    assert result.exit_code in (0, 2), result.stdout
    assert "doctor:" in result.stdout
