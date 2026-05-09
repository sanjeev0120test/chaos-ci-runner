"""Thin subprocess wrapper used by the cluster and engine modules.

Centralizing process execution keeps logging consistent and lets tests
mock a single module instead of the global subprocess API.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from dataclasses import dataclass

log = logging.getLogger("chaos_ci_runner")


class ToolMissingError(RuntimeError):
    """A required external CLI is not on PATH."""


class CommandError(RuntimeError):
    """A subprocess exited with a non-zero status."""


@dataclass
class CommandResult:
    args: list[str]
    returncode: int
    stdout: str
    stderr: str


def require(tool: str) -> str:
    """Resolve `tool` on PATH or raise ToolMissingError."""
    found = shutil.which(tool)
    if not found:
        raise ToolMissingError(
            f"required tool '{tool}' not found on PATH; install it in the CI environment"
        )
    return found


def run(
    args: list[str],
    *,
    check: bool = True,
    timeout: float | None = 600,
    env: dict[str, str] | None = None,
    input_text: str | None = None,
) -> CommandResult:
    """Run a command and capture stdout/stderr.

    On non-zero exit (with `check=True`) raises CommandError including stderr.
    """
    log.debug("exec: %s", " ".join(args))
    proc = subprocess.run(  # noqa: S603 - args list, no shell
        args,
        capture_output=True,
        text=True,
        timeout=timeout,
        env=env,
        input=input_text,
        check=False,
    )
    result = CommandResult(
        args=args,
        returncode=proc.returncode,
        stdout=proc.stdout or "",
        stderr=proc.stderr or "",
    )
    if check and proc.returncode != 0:
        raise CommandError(
            f"command failed ({proc.returncode}): {' '.join(args)}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return result
