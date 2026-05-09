# chaos-ci-runner

**CI-only chaos engineering for Kubernetes.** Spin up an ephemeral
[k3d](https://k3d.io) cluster on the GitHub Actions runner, deploy the
workload under test, exercise it with [Chaos Mesh](https://chaos-mesh.org)
and [LitmusChaos](https://litmuschaos.io), enforce SLO probes against
HTTP and PromQL, score the run 0-100, and fail the build if resilience
regresses against the previous green run on `main`.

No long-lived cluster. No operator pre-installed. No SaaS. The only
thing a team commits to their repo is a single `chaos.yaml`.

```text
PR opened → fresh k3d cluster → deploy app → baseline probes →
inject chaos (with parallel during-probes) → recovery probes →
gate + score → diff vs last green main → cluster destroyed
```

## Why this exists

Production chaos tools assume an existing cluster, an installed
operator, and a control plane that lives between runs. That is the
right model for live SRE work, and the wrong model for CI. CI is
ephemeral, hermetic, parallel, and per-PR.

`chaos-ci-runner` inverts the model: every run is a fresh blast radius,
torn down before the next one starts, with reports uploaded as build
artifacts and a PR comment so reviewers see the result without
context-switching.

## Quickstart - 30 lines, one file

`chaos.yaml`:

```yaml
cluster:
  port_mappings: ["30080:30080@server:0"]

observability:
  prometheus: true                    # in-cluster Prometheus + kube-state-metrics

target:
  manifest: ./k8s/app.yaml
  namespace: default
  ready_timeout_s: 120

steady_state:
  http_probes:
    - { name: api, url: http://localhost:30080/healthz, success_rate_pct: 99, latency_p99_ms: 200 }
  prometheus_probes:
    - name: pods-ready
      query: 'sum(kube_pod_status_ready{namespace="default", condition="true"})'
      min: 2

experiments:
  - { name: pod-kill, engine: chaos-mesh, kind: PodChaos, duration_s: 20,
      spec: { action: pod-kill, mode: one, selector: { labelSelectors: { app: my-app } } } }
  - { name: net-delay, engine: chaos-mesh, kind: NetworkChaos, duration_s: 30,
      spec: { action: delay, mode: all, selector: { labelSelectors: { app: my-app } },
              delay: { latency: "200ms" } } }

gate:
  min_pass_rate_pct: 100
  fail_on_probe_breach: true
```

`.github/workflows/chaos.yml`:

```yaml
name: chaos
on: [pull_request]
jobs:
  chaos:
    uses: sanjeev0120test/chaos-ci-runner/.github/workflows/chaos-reusable.yml@main
    with:
      config: ./chaos.yaml
```

That is the entire integration. On every PR you get a step summary, an
uploaded `chaos-report` artifact, and a comment on the PR with the
markdown report.

## CLI surface

```text
chaos-ci-runner version
chaos-ci-runner doctor                                   # preflight: docker/kubectl/helm/k3d
chaos-ci-runner validate   --config chaos.yaml
chaos-ci-runner run        --config chaos.yaml --report-dir reports/
chaos-ci-runner regression --current reports/report.json \
                           --baseline baseline/report.json \
                           --max-drop 5
```

Exit codes:

| code | meaning |
|---|---|
| `0` | gate passed (or score regression within `--max-drop`) |
| `1` | gate failed (or score dropped beyond `--max-drop`) |
| `2` | infrastructure error / missing tools / invalid config |

When the gate fails, `reports/diagnostics/` is populated with
`kubectl describe`, controller and daemon logs, and PodChaos / Job
manifests so the failure is reviewable from the artifact alone.

## Resilience score

Every run produces a single 0-100 number so resilience can be tracked
the way coverage is:

```
score = 60 · experiment_pass_rate
      + 25 · probe_success_rate
      +  15 · (1 - min(1, breaches / 5))
```

The score is embedded in `report.json` and the Markdown report header.
The reusable workflow downloads the previous successful run's artifact
and runs the regression check, so PRs that degrade resilience fail CI
before they land.

## How a single run is organised

1. Validate `chaos.yaml` against a pydantic schema.
2. `k3d cluster create` with the configured NodePort mappings.
3. (Optional) Helm-install Chaos Mesh, Prometheus + kube-state-metrics.
4. Apply the target manifest; wait for Deployments to be `Available`.
5. Run baseline HTTP and PromQL probes for the configured window.
6. Start during-chaos probes in background threads; sequence
   experiments through the configured engine.
7. Run recovery probes once experiments end.
8. Evaluate the gate (experiment pass rate + probe breaches).
9. Compute the resilience score; write `report.json` and `report.md`;
   append to `$GITHUB_STEP_SUMMARY`.
10. On PRs, post the report as a PR comment.
11. `k3d cluster delete` regardless of outcome.

## Documentation

- [docs/architecture.md](docs/architecture.md) - components and run flow
- [docs/chaos-yaml-reference.md](docs/chaos-yaml-reference.md) - full schema
- [docs/roadmap.md](docs/roadmap.md) - shipped versions and what is next

## Local development

```bash
python -m venv .venv
source .venv/bin/activate         # .venv\Scripts\activate on Windows
pip install -e ".[dev]"
ruff check chaos_ci_runner tests
ruff format --check chaos_ci_runner tests
pytest -q
chaos-ci-runner doctor
```

CI itself is the integration test: see
`.github/workflows/self-test.yml`, which runs the pipeline against
`examples/nginx/chaos.yaml` end-to-end on every push to `main`.

## License

MIT - see [LICENSE](LICENSE).
