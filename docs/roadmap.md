# Roadmap

## v1 - foundations (shipped)

- Ephemeral k3d cluster lifecycle.
- Chaos Mesh adapter (Helm install, CRD apply, status watch). Demos
  ship for both `PodChaos` (pod-kill) and `NetworkChaos` (delay).
- LitmusChaos adapter scaffolding (experiment-as-Job). Functional but
  experimental in v1: it expects the LitmusChaos operator CRDs to be
  pre-installed in the target cluster.
- HTTP probes with baseline / during / recovery windows.
- SLO gate (experiment pass rate + probe breaches).
- JSON + Markdown report. GitHub step summary integration.
- Diagnostic dump on gate failure (`reports/diagnostics/`) to make CI
  runs debuggable from artifacts alone.
- Reusable GitHub Actions workflow.
- Sample target + self-test workflow.

## v2 - observability + PromQL probes (shipped)

- Helm-installs `prometheus-community/prometheus` (no Alertmanager,
  no Pushgateway, no PVs) into the ephemeral cluster. Exposed on
  NodePort `30090` so probes can hit it from the runner.
- New `prometheus_probes` config: PromQL query + `min`/`max` bound +
  per-window success rate. Runs in baseline / during / recovery
  windows alongside HTTP probes.
- Gate handles Prometheus probe breaches as a distinct metric.

What this lets teams say:

> "During chaos, error budget consumption stayed under X%, p99 stayed
> under Y ms, and `kube_pod_status_ready` never fell below 2 of 3."

## v3 - resilience score + regression diff (shipped)

- Composite 0-100 score per run:
  `60 * experiment_pass_rate + 25 * probe_success_rate + 15 * (1 - min(1, breaches/5))`.
- Embedded in `report.json` and the Markdown report header so reviewers
  see it in the PR comment / step summary.
- New `chaos-ci-runner regression --current <json> --baseline <json>`
  CLI subcommand. `--max-drop` controls how much the score may regress
  before the command fails the build.
- Self-test workflow downloads the previous successful run's
  `chaos-report` artifact and runs the regression check automatically.

## Future

- First-class LitmusChaos via the operator's `ChaosEngine` CR (Helm
  install of `litmus-operator`, status watched on `ChaosResult`).
- AIOps: deploy [Prometheus Anomaly Detector](https://github.com/AICoE/prometheus-anomaly-detector)
  alongside Prometheus to flag unexpected metric anomalies (cascading
  failures we did not inject) during the chaos window.
- MLOps: [Evidently](https://github.com/evidentlyai/evidently) over the
  score history to detect drift in resilience trends across many
  commits.
- [Keptn Lifecycle Toolkit](https://keptn.sh) as the gate engine.
- Multi-platform CI: GitLab CI / Jenkins templates next to the GitHub
  Actions reusable workflow.
- Default Grafana dashboard rendered into the report.

## Non-goals

- Production chaos. `chaos-ci-runner` is for CI; production chaos
  belongs in tools that integrate with your real cluster, real SLOs,
  and real change management.
- A new chaos engine. We compose Chaos Mesh and Litmus rather than
  competing with them.
- A web UI. Reports are markdown and JSON. CI already has a UI.
