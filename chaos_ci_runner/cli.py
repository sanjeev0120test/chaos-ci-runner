"""chaos-ci-runner CLI entry point.

Three subcommands:

    chaos-ci-runner validate --config chaos.yaml
    chaos-ci-runner run      --config chaos.yaml --report-dir reports/
    chaos-ci-runner version

`run` is the full pipeline: cluster up -> apply target -> baseline probes
-> experiments (with parallel during-probes) -> recovery probes -> gate
-> report -> cluster down. Exit code is 0 on gate pass, 1 on gate fail,
2 on infrastructure error.
"""

from __future__ import annotations

import json
import logging
import sys
import time
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console

from chaos_ci_runner import __version__
from chaos_ci_runner.cluster import Cluster, down, up
from chaos_ci_runner.config import ChaosConfig, load_config
from chaos_ci_runner.engines import ChaosMeshEngine, LitmusEngine
from chaos_ci_runner.engines.base import (
    ChaosEngine,
    ExperimentResult,
    kubectl_apply,
    wait_for_deployment_ready,
)
from chaos_ci_runner.gate import evaluate
from chaos_ci_runner.probes import BackgroundProbe, ProbeWindow, run_http_probe
from chaos_ci_runner.report import RunReport, render_markdown, write_json, write_markdown
from chaos_ci_runner.shell import CommandError, ToolMissingError
from chaos_ci_runner.shell import run as shell_run

app = typer.Typer(
    add_completion=False,
    help="CI-only chaos engineering with Chaos Mesh and LitmusChaos.",
)
console = Console()


def _setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-5s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


@app.command()
def version() -> None:
    """Print the chaos-ci-runner version and exit."""
    typer.echo(__version__)


@app.command()
def validate(
    config: Annotated[Path, typer.Option("--config", "-c", help="Path to chaos.yaml")],
    verbose: Annotated[bool, typer.Option("--verbose", "-v")] = False,
) -> None:
    """Validate a chaos.yaml file and print the normalized view."""
    _setup_logging(verbose)
    try:
        cfg = load_config(config)
    except Exception as e:
        console.print(f"[red]invalid config:[/red] {e}")
        raise typer.Exit(code=2) from None
    console.print(f"[green]ok:[/green] {config}")
    console.print_json(data=cfg.model_dump(mode="json"))


@app.command()
def run(
    config: Annotated[Path, typer.Option("--config", "-c", help="Path to chaos.yaml")],
    report_dir: Annotated[
        Path, typer.Option("--report-dir", "-o", help="Directory to write report.{json,md}")
    ] = Path("reports"),
    keep_cluster: Annotated[
        bool, typer.Option("--keep-cluster", help="Don't tear down the k3d cluster on exit")
    ] = False,
    cluster_name: Annotated[
        str | None, typer.Option("--cluster-name", help="Override k3d cluster name")
    ] = None,
    verbose: Annotated[bool, typer.Option("--verbose", "-v")] = False,
) -> None:
    """Run the full chaos pipeline against the cluster declared in chaos.yaml."""
    _setup_logging(verbose)
    log = logging.getLogger("chaos_ci_runner")

    try:
        cfg = load_config(config)
    except Exception as e:
        console.print(f"[red]invalid config:[/red] {e}")
        raise typer.Exit(code=2) from None

    started_at = time.time()
    cluster: Cluster | None = None
    try:
        cluster = up(
            name=cluster_name,
            image=cfg.cluster.image,
            wait_timeout_s=cfg.cluster.wait_timeout_s,
            port_mappings=cfg.cluster.port_mappings,
        )
        engines = _build_engines(cfg)
        for eng in engines.values():
            eng.install(cluster)

        _apply_target(cluster, cfg)
        wait_for_deployment_ready(
            cluster,
            namespace=cfg.target.namespace,
            timeout_s=cfg.target.ready_timeout_s,
        )

        probe_windows: list[ProbeWindow] = []

        log.info("running baseline probes")
        for spec in cfg.steady_state.http_probes:
            probe_windows.append(run_http_probe(spec, window="baseline"))

        bg_probes = [
            BackgroundProbe(spec, window="during") for spec in cfg.steady_state.http_probes
        ]
        for bp in bg_probes:
            bp.start()

        results: list[ExperimentResult] = []
        try:
            for exp in cfg.experiments:
                eng = engines[exp.engine]
                log.info("running experiment '%s' via %s", exp.name, exp.engine)
                results.append(eng.run_experiment(cluster, exp))
        finally:
            for bp in bg_probes:
                probe_windows.append(bp.stop())

        log.info("running recovery probes")
        for spec in cfg.steady_state.http_probes:
            probe_windows.append(run_http_probe(spec, window="recovery"))

        outcome = evaluate(
            gate=cfg.gate,
            experiments=results,
            probe_specs=cfg.steady_state.http_probes,
            windows=probe_windows,
        )

        report = RunReport(
            config_path=str(config),
            cluster_name=cluster.name,
            started_at=started_at,
            finished_at=time.time(),
            experiments=results,
            probe_windows=probe_windows,
            gate=outcome,
        )
        json_path = write_json(report, report_dir / "report.json")
        md_path = write_markdown(report, report_dir / "report.md")
        log.info("wrote %s and %s", json_path, md_path)

        _emit_step_summary(report)
        _print_summary(report)

        if not outcome.passed:
            _dump_diagnostics(cluster, report_dir / "diagnostics")

        raise typer.Exit(code=0 if outcome.passed else 1)

    except typer.Exit:
        raise
    except (ToolMissingError, CommandError) as e:
        console.print(f"[red]infrastructure error:[/red] {e}")
        raise typer.Exit(code=2) from None
    except Exception as e:
        log.exception("unexpected error during run")
        console.print(f"[red]unexpected error:[/red] {e}")
        raise typer.Exit(code=2) from None
    finally:
        if cluster and not keep_cluster:
            down(cluster)


def _build_engines(cfg: ChaosConfig) -> dict[str, ChaosEngine]:
    needed = {exp.engine for exp in cfg.experiments}
    engines: dict[str, ChaosEngine] = {}
    if "chaos-mesh" in needed:
        engines["chaos-mesh"] = ChaosMeshEngine()
    if "litmus" in needed:
        engines["litmus"] = LitmusEngine()
    return engines


def _apply_target(cluster: Cluster, cfg: ChaosConfig) -> None:
    manifest_path = Path(cfg.target.manifest)
    if not manifest_path.is_file():
        raise FileNotFoundError(f"target manifest not found: {manifest_path}")
    payload = manifest_path.read_text(encoding="utf-8")
    kubectl_apply(cluster, payload)


def _dump_diagnostics(cluster: Cluster, out_dir: Path) -> None:
    """On gate failure, write cluster state to disk so CI artifacts include it."""
    out_dir.mkdir(parents=True, exist_ok=True)
    log = logging.getLogger("chaos_ci_runner")
    targets: list[tuple[str, list[str]]] = [
        ("get-all-namespaces.txt", ["kubectl", "get", "ns"]),
        ("get-pods-all.txt", ["kubectl", "get", "pods", "-A", "-o", "wide"]),
        ("describe-pods-chaos-mesh.txt", ["kubectl", "describe", "pods", "-n", "chaos-mesh"]),
        ("describe-pods-litmus.txt", ["kubectl", "describe", "pods", "-n", "litmus"]),
        (
            "logs-chaos-controller.txt",
            [
                "kubectl",
                "logs",
                "-n",
                "chaos-mesh",
                "-l",
                "app.kubernetes.io/component=chaos-controller-manager",
                "--tail=200",
                "--all-containers=true",
            ],
        ),
        (
            "logs-chaos-daemon.txt",
            [
                "kubectl",
                "logs",
                "-n",
                "chaos-mesh",
                "-l",
                "app.kubernetes.io/component=chaos-daemon",
                "--tail=200",
                "--all-containers=true",
            ],
        ),
        ("get-podchaos.txt", ["kubectl", "get", "podchaos", "-A", "-o", "yaml"]),
        ("get-jobs-litmus.txt", ["kubectl", "get", "jobs", "-n", "litmus", "-o", "yaml"]),
        (
            "logs-litmus-jobs.txt",
            [
                "kubectl",
                "logs",
                "-n",
                "litmus",
                "-l",
                "app=ccr-litmus",
                "--tail=200",
                "--all-containers=true",
            ],
        ),
    ]
    for filename, args in targets:
        try:
            res = shell_run(args, env=cluster.env, check=False, timeout=30)
            (out_dir / filename).write_text(
                f"$ {' '.join(args)}\n--- stdout ---\n{res.stdout}\n--- stderr ---\n{res.stderr}\n",
                encoding="utf-8",
            )
        except Exception as e:  # noqa: BLE001
            log.warning("diagnostic dump failed for %s: %s", filename, e)


def _emit_step_summary(report: RunReport) -> None:
    """If running in GitHub Actions, append the markdown report to the step summary."""
    import os

    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not summary_path:
        return
    try:
        with open(summary_path, "a", encoding="utf-8") as fh:
            fh.write(render_markdown(report))
    except OSError:
        pass


def _print_summary(report: RunReport) -> None:
    g = report.gate
    color = "green" if g.passed else "red"
    verdict = "PASS" if g.passed else "FAIL"
    console.print(f"[{color}]chaos-ci-runner: {verdict}[/{color}]")
    console.print(
        f"  experiments: {g.experiments_succeeded}/{g.experiments_total} "
        f"({g.pass_rate_pct:.1f}% pass)  breaches: {len(g.breaches)}  "
        f"duration: {report.duration_s:.1f}s"
    )
    if g.reasons:
        for r in g.reasons:
            console.print(f"  - {r}")
    if not sys.stdout.isatty():
        console.print(json.dumps(report.to_dict(), separators=(",", ":")))


if __name__ == "__main__":
    app()
