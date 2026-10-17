"""Subprocess wrapper behaviour that CI depends on."""

from __future__ import annotations

import sys

import pytest

from chaos_ci_runner.shell import CommandError, ToolMissingError, run


def test_missing_binary_is_a_tool_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def missing(*_args, **_kwargs):
        raise FileNotFoundError(2, "not found", "no-such-tool")

    monkeypatch.setattr("chaos_ci_runner.shell.subprocess.run", missing)
    with pytest.raises(ToolMissingError, match="no-such-tool"):
        run(["no-such-tool"])


def test_timeout_raises_command_error() -> None:
    with pytest.raises(CommandError, match="timed out"):
        run([sys.executable, "-c", "import time; time.sleep(5)"], timeout=0.2)
