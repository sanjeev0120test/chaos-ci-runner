"""Tests for chaos.yaml schema parsing and validation."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from chaos_ci_runner.config import ChaosConfig, load_config, resolve_manifest


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


def test_resolve_manifest_prefers_file_beside_config(tmp_path: Path) -> None:
    manifest = tmp_path / "app.yaml"
    manifest.write_text("kind: Pod\n", encoding="utf-8")
    found = resolve_manifest(tmp_path / "chaos.yaml", "app.yaml")
    assert found == manifest.resolve()


def test_resolve_manifest_falls_back_to_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg_dir = tmp_path / "nested"
    cfg_dir.mkdir()
    work = tmp_path / "work"
    work.mkdir()
    manifest = work / "app.yaml"
    manifest.write_text("kind: Pod\n", encoding="utf-8")
    monkeypatch.chdir(work)
    found = resolve_manifest(cfg_dir / "chaos.yaml", "app.yaml")
    assert found == manifest.resolve()


def test_critical_tier_defaults_max_drop_to_two(tmp_path: Path) -> None:
    cfg = _write(
        tmp_path,
        {
            "target": {"manifest": "m.yaml"},
            "experiments": [
                {
                    "name": "kill",
                    "engine": "chaos-mesh",
                    "spec": {"action": "pod-kill", "mode": "one"},
                }
            ],
            "gate": {"tier": "critical"},
        },
    )
    out = load_config(cfg)
    assert out.gate.max_score_drop == 2.0


def test_explicit_max_score_drop_overrides_tier(tmp_path: Path) -> None:
    cfg = _write(
        tmp_path,
        {
            "target": {"manifest": "m.yaml"},
            "experiments": [
                {
                    "name": "kill",
                    "engine": "chaos-mesh",
                    "spec": {"action": "pod-kill", "mode": "one"},
                }
            ],
            "gate": {"tier": "critical", "max_score_drop": 9},
        },
    )
    out = load_config(cfg)
    assert out.gate.max_score_drop == 9.0


def test_example_nginx_config_loads() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    example = repo_root / "examples" / "nginx" / "chaos.yaml"
    out = load_config(example)
    assert len(out.experiments) == 2
    engines = {e.engine for e in out.experiments}
    assert engines.issubset({"chaos-mesh", "litmus"})
    assert any(p.name == "nginx-root" for p in out.steady_state.http_probes)
