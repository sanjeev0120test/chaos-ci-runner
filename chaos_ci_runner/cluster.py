"""Ephemeral k3d cluster lifecycle.

We delegate to the `k3d` and `kubectl` CLIs to keep the Python surface
tiny. The cluster is created in `up()` and torn down in `down()`; the
returned `Cluster` carries a kubeconfig path used by everything else.
"""

from __future__ import annotations

import logging
import tempfile
import time
import uuid
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path

from chaos_ci_runner.shell import require, run

log = logging.getLogger("chaos_ci_runner")


@dataclass
class Cluster:
    name: str
    kubeconfig: Path

    @property
    def env(self) -> dict[str, str]:
        """Env vars to inject into kubectl/helm subprocess calls."""
        import os

        e = os.environ.copy()
        e["KUBECONFIG"] = str(self.kubeconfig)
        return e


def _new_name() -> str:
    return f"ccr-{uuid.uuid4().hex[:8]}"


def up(
    *,
    name: str | None = None,
    image: str = "rancher/k3s:v1.30.4-k3s1",
    wait_timeout_s: int = 180,
    api_port: int = 0,
    port_mappings: list[str] | None = None,
) -> Cluster:
    """Create a single-node k3d cluster and return a Cluster handle.

    Parameters mirror the most common knobs we need; further options can
    be added without breaking callers.
    """
    require("k3d")
    require("kubectl")

    cluster_name = name or _new_name()
    kubeconfig_path = Path(tempfile.mkdtemp(prefix="ccr-")) / "kubeconfig"

    args = [
        "k3d",
        "cluster",
        "create",
        cluster_name,
        "--image",
        image,
        "--wait",
        "--timeout",
        f"{wait_timeout_s}s",
        "--kubeconfig-update-default=false",
        "--kubeconfig-switch-context=false",
        "--k3s-arg",
        "--disable=traefik@server:0",
    ]
    if api_port:
        args.extend(["--api-port", str(api_port)])
    for mapping in port_mappings or []:
        args.extend(["--port", mapping])

    log.info("creating k3d cluster '%s'", cluster_name)
    t0 = time.time()
    run(args, timeout=wait_timeout_s + 60)

    run(
        [
            "k3d",
            "kubeconfig",
            "write",
            cluster_name,
            "--output",
            str(kubeconfig_path),
        ]
    )

    cluster = Cluster(name=cluster_name, kubeconfig=kubeconfig_path)
    _wait_for_nodes(cluster, timeout_s=wait_timeout_s)
    log.info("cluster '%s' ready in %.1fs", cluster_name, time.time() - t0)
    return cluster


def down(cluster: Cluster) -> None:
    """Delete the k3d cluster. Best-effort; logs but does not raise."""
    log.info("deleting k3d cluster '%s'", cluster.name)
    try:
        run(["k3d", "cluster", "delete", cluster.name], check=False)
    finally:
        with suppress(OSError):
            cluster.kubeconfig.unlink(missing_ok=True)


def _wait_for_nodes(cluster: Cluster, *, timeout_s: int) -> None:
    deadline = time.time() + timeout_s
    last_err = ""
    while time.time() < deadline:
        result = run(
            ["kubectl", "get", "nodes", "--no-headers"],
            env=cluster.env,
            check=False,
            timeout=15,
        )
        if result.returncode == 0 and " Ready " in f" {result.stdout} ".replace("\t", " "):
            return
        last_err = result.stderr or result.stdout
        time.sleep(2)
    raise TimeoutError(f"nodes did not become Ready within {timeout_s}s: {last_err}")
