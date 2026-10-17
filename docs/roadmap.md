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

## v0.5 - merge gate and run contract (shipped)

- The required pull-request check is lint, unit tests, and config
  validate. It installs with uv and cancels superseded runs. The
  cluster self-test stays on `main` and on runner changes, and it is
  not what merge waits on.
- Helm installs fetch a single chart. The Prometheus install skips
  node-exporter, Alertmanager, and Pushgateway.
- A probe window with no samples fails the gate and scores as 0%.
  A PromQL instant query must return exactly one series.
- `gate.tier` (`critical` / `standard` / `best-effort`) sets how far
  the score may fall. The reusable workflow diffs against the last
  green run on the default branch and updates one pull-request comment.
- `chaos-ci-runner plan` prints the experiment manifests without a
  cluster. Those objects are deleted if the status watch raises, and
  an infrastructure error still writes `reports/diagnostics/`.

## v0.6 - steady state has to come back (shipped)

- Recovery is compared with the baseline from the same run. A drop of
  more than 10 success points, or an HTTP p99 that is at least 100ms
  and 50% slower, fails the gate even when both windows sit inside
  the absolute SLO. `gate.require_steady_state_return` turns the
  failure off; the report still prints the comparison.
- Baseline and recovery probes stop when their window ends instead of
  sleeping one more full interval.

## v0.7 - fail before the cluster, and stop injecting (shipped)

- Namespaces, experiment names, probe names, and k3d port mappings are
  rejected at config load when they cannot be applied. Duplicate probe
  names are rejected. `validate` fails when a manifest namespace
  disagrees with `target.namespace`.
- Probe logs drop URL userinfo and query strings. Markdown table cells
  escape pipes. Reports posted to GitHub are capped.
- A missing binary is a tool error, and `doctor` checks that the Docker
  daemon answers, not only that the client exists.
- Rollout waits cover StatefulSets and DaemonSets as well as Deployments.
- `gate.halt_when_steady_state_lost` skips experiments that have not
  started once a during-window probe has samples and has breached.

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
