"""Tests for chaos.yaml schema parsing and validation."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from chaos_ci_runner.config import ChaosConfig, load_config


def _write(tmp_path: Path, body: dict) -> Path:
    p = tmp_path / "chaos.yaml"
    p.write_text(yaml.safe_dump(body), encoding="utf-8")
    return p


def test_minimal_chaos_mesh_config(tmp_path: Path) -> None:
    cfg = _write(
        tmp_path,
        {
            "target": {"manifest": "manifest.yaml"},
            "experiments": [
                {
                    "name": "kill",
                    "engine": "chaos-mesh",
                    "spec": {"action": "pod-kill", "mode": "one"},
                }
            ],
        },
    )
    out = load_config(cfg)
    assert isinstance(out, ChaosConfig)
    assert out.experiments[0].engine == "chaos-mesh"
    assert out.gate.min_pass_rate_pct == 100.0


def test_litmus_requires_app_label(tmp_path: Path) -> None:
    cfg = _write(
        tmp_path,
        {
            "target": {"manifest": "m.yaml"},
            "experiments": [
                {
                    "name": "lat",
                    "engine": "litmus",
                    "experiment": "pod-network-latency",
                }
            ],
        },
    )
    with pytest.raises((ValueError, ValidationError)):
        load_config(cfg)


def test_chaos_mesh_requires_spec(tmp_path: Path) -> None:
    cfg = _write(
        tmp_path,
        {
            "target": {"manifest": "m.yaml"},
            "experiments": [{"name": "x", "engine": "chaos-mesh"}],
        },
    )
    with pytest.raises((ValueError, ValidationError)):
        load_config(cfg)


def test_extra_fields_rejected(tmp_path: Path) -> None:
    cfg = _write(
        tmp_path,
        {
            "target": {"manifest": "m.yaml", "junk": 1},
            "experiments": [
                {
                    "name": "x",
                    "engine": "chaos-mesh",
                    "spec": {"action": "pod-kill", "mode": "one"},
                }
            ],
        },
    )
    with pytest.raises(ValidationError):
        load_config(cfg)


def test_example_nginx_config_loads() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    example = repo_root / "examples" / "nginx" / "chaos.yaml"
    out = load_config(example)
    assert len(out.experiments) == 2
    engines = {e.engine for e in out.experiments}
    assert engines == {"chaos-mesh", "litmus"}
    assert any(p.name == "nginx-root" for p in out.steady_state.http_probes)
