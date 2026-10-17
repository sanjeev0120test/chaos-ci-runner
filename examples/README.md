# Examples

Each subdirectory is a self-contained chaos run. The self-test workflow
runs `nginx/`.

## `nginx/`

A 3-replica Nginx Deployment exposed on NodePort `30080`, plus an
in-cluster Prometheus. `chaos.yaml` runs two Chaos Mesh experiments:

- `pod-kill-one` (`PodChaos`, action `pod-kill`, mode `one`)
- `network-delay-200ms` (`NetworkChaos`, action `delay`, 200ms)

HTTP probes hit `http://localhost:30080/`. A PromQL probe requires at
least two nginx pods Ready. The gate requires every experiment to
succeed, probe SLOs to hold, and recovery to return to that run's
baseline. Halt-on-loss is off, so both experiments run.

```bash
chaos-ci-runner validate --config examples/nginx/chaos.yaml
chaos-ci-runner plan --config examples/nginx/chaos.yaml
chaos-ci-runner run --config examples/nginx/chaos.yaml --report-dir reports/
```

`run` needs Docker, kubectl, Helm, and k3d. `chaos-ci-runner doctor`
checks those before a cluster is created.
