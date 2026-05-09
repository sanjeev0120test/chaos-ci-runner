"""chaos-ci-runner CLI entry point.

Subcommands:

    chaos-ci-runner version
    chaos-ci-runner doctor
    chaos-ci-runner validate   --config chaos.yaml
    chaos-ci-runner run        --config chaos.yaml --report-dir reports/
    chaos-ci-runner regression --current reports/report.json \\
                               --baseline baseline/report.json \\
                               --max-drop 5

`run` is the full pipeline: cluster up -> apply target -> baseline probes
-> experiments (with parallel during-probes) -> recovery probes -> gate
-> resilience score -> report -> cluster down. Exit code is 0 on gate
pass, 1 on gate fail, 2 on infrastructure error.
"""

from __future__ import annotations

import json
import logging
import os
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
from chaos_ci_runner.observability import PrometheusInstaller
from chaos_ci_runner.probes import (
    BackgroundProbe,
    BackgroundPrometheusProbe,
    ProbeWindow,
    run_http_probe,
    run_prometheus_probe,
)
from chaos_ci_runner.report import RunReport, render_markdown, write_json, write_markdown
from chaos_ci_runner.score import compute as compute_score
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
def doctor(
    verbose: Annotated[bool, typer.Option("--verbose", "-v")] = False,
) -> None:
    """Check that the runner has every tool it needs to spin up a cluster.

    Useful as a CI preflight step or when integrating chaos-ci-runner into
    a new pipeline. Exits 0 only when all required tools are present.
    """
    _setup_logging(verbose)
    required = [
        ("docker", ["docker", "version", "--format", "{{.Client.Version}}"]),
        ("kubectl", ["kubectl", "version", "--client=true", "-o", "json"]),
        ("helm", ["helm", "version", "--short"]),
        ("k3d", ["k3d", "version"]),
    ]
    import shutil

    rows: list[tuple[str, str, str]] = []
    missing = 0
    for tool, args in required:
        if shutil.which(tool) is None:
            rows.append((tool, "missing", "not on PATH"))
            missing += 1
            continue
        try:
            res = shell_run(args, check=False, timeout=10)
            ok = res.returncode == 0
            line = (res.stdout or res.stderr).strip().splitlines()[:1]
            rows.append((tool, "ok" if ok else "fail", line[0] if line else ""))
            if not ok:
                missing += 1
        except (ToolMissingError, FileNotFoundError, OSError) as e:
            rows.append((tool, "missing", str(e)[:60]))
            missing += 1

    width = max(len(r[0]) for r in rows)
    for tool, status, detail in rows:
        color = {"ok": "green", "fail": "red", "missing": "red"}[status]
        console.print(f"  [{color}]{status:7}[/{color}]  {tool:<{width}}  {detail}")
    if missing:
        console.print(f"[red]doctor: {missing} tool(s) missing or unhealthy.[/red]")
        raise typer.Exit(code=2)
    console.print("[green]doctor: all required tools present.[/green]")


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
        port_mappings = list(cfg.cluster.port_mappings)
        prom_installer: PrometheusInstaller | None = None
        if cfg.observability.prometheus:
            prom_installer = PrometheusInstaller(node_port=cfg.observability.prometheus_node_port)
            if prom_installer.required_port_mapping not in port_mappings:
                port_mappings.append(prom_installer.required_port_mapping)

        cluster = up(
            name=cluster_name,
            image=cfg.cluster.image,
            wait_timeout_s=cfg.cluster.wait_timeout_s,
            port_mappings=port_mappings,
        )
        engines = _build_engines(cfg)
        for eng in engines.values():
            eng.install(cluster)
        if prom_installer is not None:
            prom_installer.install(cluster)

        _apply_target(cluster, cfg)
        wait_for_deployment_ready(
            cluster,
            namespace=cfg.target.namespace,
            timeout_s=cfg.target.ready_timeout_s,
        )

        probe_windows: list[ProbeWindow] = []
        prom_url = prom_installer.base_url if prom_installer is not None else ""

        log.info("running baseline probes")
        for spec in cfg.steady_state.http_probes:
            probe_windows.append(run_http_probe(spec, window="baseline"))
        for pspec in cfg.steady_state.prometheus_probes:
            probe_windows.append(run_prometheus_probe(pspec, base_url=prom_url, window="baseline"))

        bg_http = [BackgroundProbe(spec, window="during") for spec in cfg.steady_state.http_probes]
        bg_prom = [
            BackgroundPrometheusProbe(pspec, base_url=prom_url, window="during")
            for pspec in cfg.steady_state.prometheus_probes
        ]
        for bp in bg_http:
            bp.start()
        for bp in bg_prom:
            bp.start()

        results: list[ExperimentResult] = []
        try:
            for exp in cfg.experiments:
                eng = engines[exp.engine]
                log.info("running experiment '%s' via %s", exp.name, exp.engine)
                results.append(eng.run_experiment(cluster, exp))
        finally:
            for bp in bg_http:
                probe_windows.append(bp.stop())
            for bp in bg_prom:
                probe_windows.append(bp.stop())

        log.info("running recovery probes")
        for spec in cfg.steady_state.http_probes:
            probe_windows.append(run_http_probe(spec, window="recovery"))
        for pspec in cfg.steady_state.prometheus_probes:
            probe_windows.append(run_prometheus_probe(pspec, base_url=prom_url, window="recovery"))

        outcome = evaluate(
            gate=cfg.gate,
            experiments=results,
            http_probe_specs=cfg.steady_state.http_probes,
            prometheus_probe_specs=cfg.steady_state.prometheus_probes,
            windows=probe_windows,
        )

        score = compute_score(experiments=results, windows=probe_windows, gate=outcome)
        report = RunReport(
            config_path=str(config),
            cluster_name=cluster.name,
            started_at=started_at,
            finished_at=time.time(),
            experiments=results,
            probe_windows=probe_windows,
            gate=outcome,
            score=score,
            commit_sha=os.environ.get("GITHUB_SHA"),
        )
        json_path = write_json(report, report_dir / "report.json")
        md_path = write_markdown(report, report_dir / "report.md")
        log.info("wrote %s and %s (score=%.1f)", json_path, md_path, score.score)

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


@app.command()
def regression(
    current: Annotated[Path, typer.Option("--current", help="Current run's report.json")] = Path(
        "reports/report.json"
    ),
    baseline: Annotated[
        Path, typer.Option("--baseline", help="Previous run's report.json to diff against")
    ] = Path("baseline/report.json"),
    max_drop: Annotated[
        float,
        typer.Option(
            "--max-drop",
            help="Max acceptable score drop (points). Exceeding this exits non-zero.",
        ),
    ] = 5.0,
    verbose: Annotated[bool, typer.Option("--verbose", "-v")] = False,
) -> None:
    """Compare current run's resilience score to a baseline; flag regressions."""
    _setup_logging(verbose)
    if not current.is_file():
        console.print(f"[red]current report not found: {current}[/red]")
        raise typer.Exit(code=2)
    if not baseline.is_file():
        console.print(f"[yellow]baseline missing ({baseline}); accepting as first run.[/yellow]")
        raise typer.Exit(code=0)

    cur_data = json.loads(current.read_text(encoding="utf-8"))
    base_data = json.loads(baseline.read_text(encoding="utf-8"))
    cur_score = (cur_data.get("score") or {}).get("score")
    base_score = (base_data.get("score") or {}).get("score")
    if cur_score is None:
        console.print(
            "[red]current report does not contain a score; was --report-dir generated by v3+?[/red]"
        )
        raise typer.Exit(code=2)
    if base_score is None:
        console.print(
            f"[yellow]baseline {baseline} has no score (pre-v3 report); "
            f"accepting current score {cur_score:.1f}.[/yellow]"
        )
        raise typer.Exit(code=0)

    drop = base_score - cur_score
    cur_sha = cur_data.get("commit_sha") or "current"
    base_sha = base_data.get("commit_sha") or "baseline"
    msg = f"score {cur_sha[:7] if cur_sha != 'current' else cur_sha}={cur_score:.1f}  vs  {base_sha[:7] if base_sha != 'baseline' else base_sha}={base_score:.1f}  drop={drop:+.1f}"
    if drop > max_drop:
        console.print(f"[red]REGRESSION:[/red] {msg} (max-drop {max_drop:+.1f})")
        raise typer.Exit(code=1)
    console.print(f"[green]ok:[/green] {msg}")


if __name__ == "__main__":
    app()
