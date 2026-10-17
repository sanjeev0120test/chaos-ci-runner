"""Tests for chaos.yaml schema parsing and validation."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from chaos_ci_runner.config import ChaosConfig, load_config, namespace_mismatches, resolve_manifest


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


def test_duplicate_experiment_names_rejected(tmp_path: Path) -> None:
    cfg = _write(
        tmp_path,
        {
            "target": {"manifest": "m.yaml"},
            "experiments": [
                {
                    "name": "kill",
                    "engine": "chaos-mesh",
                    "spec": {"action": "pod-kill", "mode": "one"},
                },
                {
                    "name": "kill",
                    "engine": "chaos-mesh",
                    "spec": {"action": "pod-kill", "mode": "all"},
                },
            ],
        },
    )
    with pytest.raises((ValueError, ValidationError), match="unique"):
        load_config(cfg)


def _mesh(name: str = "kill") -> dict:
    return {
        "name": name,
        "engine": "chaos-mesh",
        "spec": {"action": "pod-kill", "mode": "one"},
    }


def test_namespace_must_be_dns1123(tmp_path: Path) -> None:
    cfg = _write(
        tmp_path,
        {"target": {"manifest": "m.yaml", "namespace": "Not_Valid"}, "experiments": [_mesh()]},
    )
    with pytest.raises((ValueError, ValidationError), match="DNS-1123"):
        load_config(cfg)


def test_duplicate_probe_names_rejected(tmp_path: Path) -> None:
    cfg = _write(
        tmp_path,
        {
            "observability": {"prometheus": True},
            "target": {"manifest": "m.yaml"},
            "steady_state": {
                "http_probes": [{"name": "api", "url": "http://localhost/"}],
                "prometheus_probes": [{"name": "api", "query": "up", "min": 1}],
            },
            "experiments": [_mesh()],
        },
    )
    with pytest.raises((ValueError, ValidationError), match="probe names must be unique"):
        load_config(cfg)


def test_port_mapping_must_match_k3d(tmp_path: Path) -> None:
    cfg = _write(
        tmp_path,
        {
            "cluster": {"port_mappings": ["not a port"]},
            "target": {"manifest": "m.yaml"},
            "experiments": [_mesh()],
        },
    )
    with pytest.raises((ValueError, ValidationError), match="port mapping"):
        load_config(cfg)


def test_namespace_mismatch_names_the_resource() -> None:
    text = "apiVersion: v1\nkind: Service\nmetadata:\n  name: api\n  namespace: other\n"
    assert namespace_mismatches(text, "default") == [
        "Service/api namespace 'other' does not match target.namespace 'default'"
    ]
    assert namespace_mismatches(text, "other") == []


def test_example_nginx_config_loads() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    example = repo_root / "examples" / "nginx" / "chaos.yaml"
    out = load_config(example)
    assert len(out.experiments) == 2
    engines = {e.engine for e in out.experiments}
    assert engines.issubset({"chaos-mesh", "litmus"})
    assert any(p.name == "nginx-root" for p in out.steady_state.http_probes)
