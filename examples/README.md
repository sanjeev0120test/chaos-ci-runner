# Examples

Each subdirectory is a self-contained chaos run that the project's CI uses for self-testing.

## `nginx/`

A 3-replica Nginx Deployment exposed on `NodePort 30080`.

- `manifest.yaml` - target workload.
- `chaos.yaml` - two experiments:
  - `pod-kill-one` via Chaos Mesh (`PodChaos`, action `pod-kill`)
  - `network-latency-200ms` via LitmusChaos (`pod-network-latency`)

Run locally inside the chaos-ci-runner Docker image, or in CI via the
self-test workflow:

```bash
chaos-ci-runner run --config examples/nginx/chaos.yaml --report-dir reports/
```
