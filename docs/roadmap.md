# Roadmap

v1 is intentionally lean. The roadmap explicitly anchors where the
project is going so contributors and adopters can plan around it.

## v1 (this release)

Shipped:

- Ephemeral k3d cluster lifecycle.
- Chaos Mesh adapter (Helm install, CRD apply, status watch). Demos
  ship for both `PodChaos` (pod-kill) and `NetworkChaos` (delay).
- LitmusChaos adapter scaffolding (experiment-as-Job). Functional but
  experimental in v1: it expects the LitmusChaos operator CRDs to be
  pre-installed in the target cluster. First-class Litmus support
  (Helm install of `litmus-operator` + `ChaosEngine` CR) lands in v2.
- HTTP probes with baseline / during / recovery windows.
- SLO gate (experiment pass rate + probe breaches).
- JSON + Markdown report. GitHub step summary integration.
- Diagnostic dump on gate failure (`reports/diagnostics/`) to make CI
  runs debuggable from artifacts alone.
- Reusable GitHub Actions workflow.
- Sample target + self-test workflow.

## v2 - Observability + first-class Litmus

Goal: replace the v1 HTTP probe with real observability, let gates be
expressed against real SLOs, and finish the LitmusChaos integration.

- Install [LitmusChaos operator](https://litmuschaos.io) via Helm and
  switch the engine to apply `ChaosEngine` CRs. Status watched via
  `ChaosResult`. Enables the full ChaosHub experiment catalog with no
  per-experiment plumbing.
- Deploy Prometheus + OpenTelemetry Collector into the ephemeral
  cluster as part of `cluster.up`. (CNCF, open source, no SaaS.)
- Add a `prometheus` probe type: a PromQL query and a numeric SLO
  threshold (e.g. `sum(rate(http_requests_total{status=~"5.."}[1m])) < 0.01`).
- Integrate [Keptn](https://keptn.sh) Lifecycle Toolkit for SLO-based
  evaluation. Keptn becomes the gate engine; v1's `gate.py` shrinks to
  a thin adapter.
- Optional: ship a default Grafana dashboard rendered into the report.

What this lets teams say:

> "During chaos, error budget consumption stayed under X%, p99 stayed
> under Y ms. Pass."

## v3 - AIOps anomaly detection + resilience regression

Goal: detect cascading failures we did not inject, and track resilience
across commits.

- Run [Prometheus Anomaly Detector (PAD)](https://github.com/AICoE/prometheus-anomaly-detector)
  alongside Prometheus to flag unexpected metric anomalies during the
  chaos window. Surface them in the report as a new "Unexpected
  anomalies" table.
- Compute a single resilience score per run (composite of pass rate,
  recovery time, breach count, anomaly count). Persist the score as a
  CI artifact keyed by commit SHA.
- Add a `chaos-ci-runner regression --against <ref>` command that
  downloads the prior run's score from CI artifacts (or a small object
  store) and diffs the two: "p99 recovery degraded 2.1s -> 9.3s on this
  PR."
- Optional MLOps tie-in: run [Evidently](https://github.com/evidentlyai/evidently)
  over the score history to detect drift in resilience trends across
  many commits.

## Non-goals

- Production chaos. `chaos-ci-runner` is for CI; production chaos
  belongs in tools that integrate with your real cluster, real SLOs,
  and real change management.
- A new chaos engine. We compose Chaos Mesh and Litmus rather than
  competing with them.
- A web UI. Reports are markdown and JSON. CI already has a UI.
