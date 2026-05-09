"""Steady-state probes.

A probe is a small repeated measurement of the target's health. We keep
the v1 surface to a single HTTP probe that captures success rate and
p99 latency over a fixed window. Probes are run in three windows:

    * baseline   - before chaos starts
    * during     - while at least one experiment is active
    * recovery   - after chaos stops, to verify steady state returns

The output is a `ProbeWindow` per name+window pair; the gate consumes it.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Literal

import requests

from chaos_ci_runner.config import HttpProbeSpec

log = logging.getLogger("chaos_ci_runner")

WindowName = Literal["baseline", "during", "recovery"]


@dataclass
class ProbeSample:
    ok: bool
    latency_ms: float
    status: int
    error: str = ""


@dataclass
class ProbeWindow:
    probe: str
    window: WindowName
    samples: list[ProbeSample] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.samples)

    @property
    def successes(self) -> int:
        return sum(1 for s in self.samples if s.ok)

    @property
    def success_rate_pct(self) -> float:
        return 100.0 * self.successes / self.total if self.total else 0.0

    @property
    def latency_p99_ms(self) -> float:
        if not self.samples:
            return 0.0
        sorted_ms = sorted(s.latency_ms for s in self.samples)
        idx = max(0, int(round(0.99 * (len(sorted_ms) - 1))))
        return sorted_ms[idx]

    def to_dict(self) -> dict:
        return {
            "probe": self.probe,
            "window": self.window,
            "total": self.total,
            "successes": self.successes,
            "success_rate_pct": round(self.success_rate_pct, 3),
            "latency_p99_ms": round(self.latency_p99_ms, 3),
        }


def run_http_probe(spec: HttpProbeSpec, *, window: WindowName) -> ProbeWindow:
    """Run a single HTTP probe synchronously for `spec.duration_s`."""
    out = ProbeWindow(probe=spec.name, window=window)
    end = time.time() + spec.duration_s
    interval = spec.interval_ms / 1000.0
    timeout = spec.timeout_ms / 1000.0
    log.info(
        "[probe:%s] window=%s url=%s duration=%ss",
        spec.name,
        window,
        spec.url,
        spec.duration_s,
    )
    while time.time() < end:
        t0 = time.time()
        try:
            resp = requests.get(spec.url, timeout=timeout)
            latency_ms = (time.time() - t0) * 1000.0
            ok = 200 <= resp.status_code < 400
            out.samples.append(ProbeSample(ok=ok, latency_ms=latency_ms, status=resp.status_code))
        except requests.RequestException as e:
            latency_ms = (time.time() - t0) * 1000.0
            out.samples.append(ProbeSample(ok=False, latency_ms=latency_ms, status=0, error=str(e)))
        time.sleep(interval)
    return out


class BackgroundProbe:
    """Run an HTTP probe in a thread; collect a ProbeWindow at the end.

    Used for the `during` window so the probe runs in parallel with
    experiments rather than serially after them.
    """

    def __init__(self, spec: HttpProbeSpec, *, window: WindowName = "during") -> None:
        self.spec = spec
        self.window = window
        self._stop = threading.Event()
        self._result = ProbeWindow(probe=spec.name, window=window)
        self._thread = threading.Thread(target=self._run, daemon=True, name=f"probe-{spec.name}")

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> ProbeWindow:
        self._stop.set()
        self._thread.join(timeout=self.spec.timeout_ms / 1000.0 + 5)
        return self._result

    def _run(self) -> None:
        interval = self.spec.interval_ms / 1000.0
        timeout = self.spec.timeout_ms / 1000.0
        while not self._stop.is_set():
            t0 = time.time()
            try:
                resp = requests.get(self.spec.url, timeout=timeout)
                latency_ms = (time.time() - t0) * 1000.0
                ok = 200 <= resp.status_code < 400
                self._result.samples.append(
                    ProbeSample(ok=ok, latency_ms=latency_ms, status=resp.status_code)
                )
            except requests.RequestException as e:
                latency_ms = (time.time() - t0) * 1000.0
                self._result.samples.append(
                    ProbeSample(ok=False, latency_ms=latency_ms, status=0, error=str(e))
                )
            if self._stop.wait(interval):
                return
