# `chaos.yaml` reference

Authoritative schema is [`chaos_ci_runner/config.py`](../chaos_ci_runner/config.py)
(pydantic v2 with `extra: forbid` everywhere - typos are surfaced
immediately).

```yaml
cluster:           # optional
  image: rancher/k3s:v1.30.4-k3s1
  wait_timeout_s: 180
  port_mappings: ["30080:30080@server:0"]

target:            # required
  manifest: ./k8s/app.yaml
  namespace: default
  ready_timeout_s: 60

steady_state:      # optional
  http_probes:
    - name: api-health
      url: http://localhost:30080/healthz
      success_rate_pct: 99
      latency_p99_ms: 200
      duration_s: 15
      interval_ms: 200
      timeout_ms: 2000

experiments:       # required, at least one
  - name: pod-kill
    engine: chaos-mesh
    duration_s: 30
    kind: PodChaos       # default; override for NetworkChaos / StressChaos / etc.
    spec: { ... }        # raw Chaos Mesh CRD spec body

  - name: net-latency
    engine: litmus
    duration_s: 60
    experiment: pod-network-latency   # ChaosHub experiment name
    app_label: app=my-app             # required for litmus
    params:                           # passed as env vars to the experiment
      NETWORK_LATENCY: "200"
      APP_NAMESPACE: default

gate:
  min_pass_rate_pct: 100
  fail_on_probe_breach: true
```

## Fields

### `cluster`

| field | default | meaning |
|---|---|---|
| `image` | `rancher/k3s:v1.30.4-k3s1` | k3s node image |
| `wait_timeout_s` | `180` | how long to wait for nodes Ready |
| `port_mappings` | `[]` | passed verbatim as `k3d cluster create --port`. Required if probes hit `localhost:<NodePort>`. |

### `target`

| field | required | meaning |
|---|---|---|
| `manifest` | yes | path (relative to repo root) to a Kubernetes manifest file |
| `namespace` | no, default `default` | namespace passed to `kubectl wait` |
| `ready_timeout_s` | no, default `60` | timeout for all Deployments in the namespace to become Available |

### `steady_state.http_probes`

| field | meaning |
|---|---|
| `name` | logical probe name (used in reports) |
| `url` | URL to GET |
| `success_rate_pct` | min acceptable percentage of HTTP 2xx/3xx |
| `latency_p99_ms` | max acceptable p99 latency in milliseconds |
| `duration_s` | how long the baseline / recovery windows run |
| `interval_ms` | gap between samples |
| `timeout_ms` | per-request timeout |

### `experiments[]`

Common fields:

| field | meaning |
|---|---|
| `name` | unique label for the experiment |
| `engine` | `chaos-mesh` or `litmus` |
| `duration_s` | how long the experiment is active |

Engine-specific fields:

| engine | required | meaning |
|---|---|---|
| `chaos-mesh` | `spec` | raw `spec` body of the Chaos Mesh CRD |
| `chaos-mesh` | `kind` (optional, default `PodChaos`) | which Chaos Mesh CRD kind |
| `litmus` | `experiment` | ChaosHub experiment name |
| `litmus` | `app_label` | target selector, e.g. `app=nginx` |
| `litmus` | `params` (optional) | env-var-shaped params for the experiment |

### `gate`

| field | default | meaning |
|---|---|---|
| `min_pass_rate_pct` | `100.0` | minimum experiment success rate |
| `fail_on_probe_breach` | `true` | turn probe SLO breaches into a fail |

## Tips

- Start with one experiment and a single probe. Make it pass and fail
  deliberately before adding more.
- Use `chaos-ci-runner validate --config chaos.yaml` to catch typos
  without spinning up a cluster.
- For Chaos Mesh, copy the `spec` directly from the
  [official examples](https://chaos-mesh.org/docs/) - the schema is
  pass-through.
- For Litmus, env-var names match the
  [ChaosHub experiment docs](https://hub.litmuschaos.io/).
