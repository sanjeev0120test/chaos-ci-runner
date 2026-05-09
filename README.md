# chaos-ci-runner

CI-only chaos engineering for Kubernetes workloads.

`chaos-ci-runner` spins up an ephemeral [k3d](https://k3d.io) cluster
inside your CI runner, deploys your workload, runs chaos experiments
through [Chaos Mesh](https://chaos-mesh.org) and
[LitmusChaos](https://litmuschaos.io), evaluates steady-state SLO probes,
and emits a pass/fail resilience report. Nothing is installed on
developer machines; the only artifact a team commits is a `chaos.yaml`.

> Status: v3. v1 shipped Chaos Mesh + LitmusChaos + HTTP probes; v2
> added in-cluster Prometheus and PromQL probes; v3 added a composite
> resilience score and a regression-diff CLI. See
> [`docs/roadmap.md`](docs/roadmap.md).

## Why

Most chaos tools assume an existing cluster, an installed operator, and
a long-lived control plane. `chaos-ci-runner` is built around the
inverse: every run is fresh, ephemeral, and lives entirely inside CI.
Teams adopt resilience testing with a single PR.

## Quickstart

In any GitHub repo with a Kubernetes manifest, add `chaos.yaml`:

```yaml
cluster:
  port_mappings:
    - "30080:30080@server:0"

target:
  manifest: ./k8s/app.yaml
  namespace: default
  ready_timeout_s: 120

steady_state:
  http_probes:
    - name: api-health
      url: http://localhost:30080/healthz
      success_rate_pct: 99
      latency_p99_ms: 200
      duration_s: 15

experiments:
  - name: pod-kill
    engine: chaos-mesh
    kind: PodChaos
    duration_s: 20
    spec:
      action: pod-kill
      mode: one
      selector:
        labelSelectors: { app: my-app }

  - name: network-delay
    engine: chaos-mesh
    kind: NetworkChaos
    duration_s: 30
    spec:
      action: delay
      mode: all
      selector:
        labelSelectors: { app: my-app }
      delay:
        latency: "200ms"

gate:
  min_pass_rate_pct: 100
  fail_on_probe_breach: true
```

To use PromQL gates (v2), enable the in-cluster Prometheus and add a
`prometheus_probes` block:

```yaml
observability:
  prometheus: true

steady_state:
  prometheus_probes:
    - name: pods-ready
      query: 'sum(kube_pod_status_ready{namespace="default", condition="true"})'
      min: 2
      duration_s: 20
```

Then add `.github/workflows/chaos.yml`:

```yaml
name: chaos
on: [pull_request]
jobs:
  chaos:
    uses: sanjeev0120test/chaos-ci-runner/.github/workflows/chaos-reusable.yml@main
    with:
      config: ./chaos.yaml
```

That's it. On every PR, an ephemeral k3d cluster is created, your
workload deployed, both Chaos Mesh and Litmus experiments run, probes
captured, the gate evaluated, and a report uploaded as an artifact.

## CLI

```text
chaos-ci-runner version
chaos-ci-runner validate   --config chaos.yaml
chaos-ci-runner run        --config chaos.yaml --report-dir reports/
chaos-ci-runner regression --current reports/report.json \
                           --baseline baseline/report.json \
                           --max-drop 5
```

Exit codes: `0` gate passed (or score within tolerance), `1` gate
failed (or score regressed beyond `--max-drop`), `2` infrastructure
error.

## Resilience score

Every run produces a 0-100 composite score:

```
score = 60 * experiment_pass_rate
      + 25 * probe_success_rate
      + 15 * (1 - min(1, breaches/5))
```

Embedded in `report.json` and the Markdown header. The reusable
workflow downloads the previous successful run's artifact and runs the
regression check, so PRs that degrade resilience fail CI before they
land.

## How it works

1. Read and validate `chaos.yaml` (pydantic schema).
2. Create an ephemeral k3d cluster with optional NodePort mappings.
3. Apply the target manifest and wait for Deployments to be Available.
4. Install the engines required by the experiment list (Chaos Mesh via
   Helm; Litmus only namespace + RBAC for Job-based experiments).
5. Run baseline HTTP probes for the configured duration.
6. Start during-chaos probes in background threads, then run each
   experiment sequentially.
7. Run recovery probes once experiments end.
8. Evaluate the gate: experiment success rate plus probe SLO breaches.
9. Write `report.json` and `report.md` and append the markdown to
   `GITHUB_STEP_SUMMARY` when running in Actions.
10. Destroy the cluster.

## Documentation

- [docs/architecture.md](docs/architecture.md) - components and run flow
- [docs/chaos-yaml-reference.md](docs/chaos-yaml-reference.md) - schema reference
- [docs/roadmap.md](docs/roadmap.md) - shipped versions and what's next

## Development

```bash
python -m venv .venv
source .venv/bin/activate    # or .venv\Scripts\activate on Windows
pip install -e ".[dev]"
ruff check chaos_ci_runner tests
pytest -q
```

## License

MIT - see [LICENSE](LICENSE).
