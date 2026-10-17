"""Subprocess wrapper behaviour that CI depends on."""

from __future__ import annotations

import sys

import pytest

from chaos_ci_runner.shell import CommandError, run


def test_timeout_raises_command_error() -> None:
    with pytest.raises(CommandError, match="timed out"):
        run([sys.executable, "-c", "import time; time.sleep(5)"], timeout=0.2)
