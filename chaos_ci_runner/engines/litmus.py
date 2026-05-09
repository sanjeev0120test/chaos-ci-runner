"""LitmusChaos engine adapter: experiments-as-Jobs (no full operator).

For v1 we deliberately avoid installing the entire Litmus operator/portal.
Instead, we run individual `litmuschaos/go-runner` experiments as a single
Kubernetes Job, parameterized by env vars exactly the way the operator
would set them up. This is the smallest viable use of Litmus in CI and
it covers most pod-level experiments in the ChaosHub catalog.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from chaos_ci_runner.cluster import Cluster
from chaos_ci_runner.config import ExperimentSpec
from chaos_ci_runner.engines.base import (
    ExperimentResult,
    kubectl_apply,
    kubectl_delete,
    kubectl_get_json,
)

log = logging.getLogger("chaos_ci_runner")

LITMUS_NAMESPACE = "litmus"
LITMUS_RUNNER_IMAGE = "litmuschaos/go-runner:3.10.0"
LITMUS_SA = "litmus-experiment-sa"


def _split_label(label: str) -> tuple[str, str]:
    if "=" not in label:
        raise ValueError(f"app_label must be 'key=value', got: {label!r}")
    k, v = label.split("=", 1)
    return k.strip(), v.strip()


class LitmusEngine:
    name = "litmus"

    def __init__(self, *, runner_image: str = LITMUS_RUNNER_IMAGE) -> None:
        self.runner_image = runner_image
        self._installed = False

    def install(self, cluster: Cluster) -> None:
        if self._installed:
            return
        log.info("preparing litmus namespace and RBAC in cluster")
        kubectl_apply(cluster, _namespace_manifest(LITMUS_NAMESPACE))
        kubectl_apply(cluster, _rbac_manifests(LITMUS_NAMESPACE, LITMUS_SA))
        self._installed = True

    def run_experiment(self, cluster: Cluster, exp: ExperimentSpec) -> ExperimentResult:
        assert exp.experiment is not None, "experiment name is required for litmus"
        assert exp.app_label is not None, "app_label is required for litmus"
        label_key, label_value = _split_label(exp.app_label)
        target_namespace = exp.params.get("APP_NAMESPACE", "default")

        params = {
            "TOTAL_CHAOS_DURATION": str(exp.duration_s),
            "CHAOS_INTERVAL": str(max(1, exp.duration_s // 2)),
            "RAMP_TIME": "0",
            "FORCE": "false",
            "APP_NAMESPACE": target_namespace,
            "APP_LABEL": exp.app_label,
            "APP_KIND": "deployment",
            "TARGET_CONTAINER": "",
            "PODS_AFFECTED_PERC": "100",
            "CHAOS_NAMESPACE": LITMUS_NAMESPACE,
            "INSTANCE_ID": exp.name,
        }
        params.update(exp.params)

        job_name = f"ccr-litmus-{exp.name.replace('_', '-')}-{int(time.time())}"
        manifest = _experiment_job(
            name=job_name,
            namespace=LITMUS_NAMESPACE,
            sa=LITMUS_SA,
            image=self.runner_image,
            experiment=exp.experiment,
            params=params,
            label_key=label_key,
            label_value=label_value,
        )

        started = time.time()
        log.info(
            "[litmus] running experiment '%s' (%s) for %ss",
            exp.experiment,
            job_name,
            exp.duration_s,
        )
        kubectl_apply(cluster, manifest)

        budget_s = exp.duration_s + 180
        status = "unknown"
        message = ""
        last_obj: dict[str, Any] = {}
        deadline = started + budget_s
        last_log_time = 0.0
        while time.time() < deadline:
            last_obj = kubectl_get_json(cluster, "job", job_name, namespace=LITMUS_NAMESPACE)
            st = last_obj.get("status", {}) if last_obj else {}
            active = st.get("active", 0)
            succeeded = st.get("succeeded", 0)
            failed = st.get("failed", 0)
            if time.time() - last_log_time > 10:
                log.info(
                    "[litmus] %s active=%s succeeded=%s failed=%s",
                    job_name,
                    active,
                    succeeded,
                    failed,
                )
                last_log_time = time.time()
            if succeeded:
                status = "succeeded"
                break
            if failed:
                status = "failed"
                message = _explain_litmus_failure(cluster, job_name)
                log.warning("[litmus] job %s failed: %s", job_name, message)
                break
            time.sleep(2)
        else:
            status = "timeout"
            message = f"litmus job did not finish within {budget_s}s"
            log.warning("[litmus] %s", message)

        kubectl_delete(cluster, "job", job_name, namespace=LITMUS_NAMESPACE)
        finished = time.time()
        return ExperimentResult(
            name=exp.name,
            engine=self.name,
            started_at=started,
            finished_at=finished,
            status=status,
            message=message,
            raw=last_obj,
        )

    def cleanup(self, cluster: Cluster) -> None:
        self._installed = False


def _explain_litmus_failure(cluster: Cluster, job_name: str) -> str:
    """Best-effort: include the last 50 lines of the experiment pod's logs."""
    from chaos_ci_runner.shell import run as _run

    try:
        pods = _run(
            [
                "kubectl",
                "get",
                "pods",
                "-n",
                LITMUS_NAMESPACE,
                "-l",
                f"job-name={job_name}",
                "-o",
                "name",
            ],
            env=cluster.env,
            check=False,
            timeout=15,
        )
        names = [n.strip() for n in pods.stdout.splitlines() if n.strip()]
        if not names:
            return "litmus job reported failure (no pod found for logs)"
        logs = _run(
            ["kubectl", "logs", names[0], "-n", LITMUS_NAMESPACE, "--tail=50"],
            env=cluster.env,
            check=False,
            timeout=20,
        )
        tail = (logs.stdout or logs.stderr or "").strip().splitlines()[-20:]
        return "litmus job reported failure; last log lines: " + " | ".join(tail)
    except Exception as e:  # noqa: BLE001
        return f"litmus job reported failure ({e})"


def _namespace_manifest(name: str) -> dict[str, Any]:
    return {
        "apiVersion": "v1",
        "kind": "Namespace",
        "metadata": {"name": name},
    }


def _rbac_manifests(namespace: str, sa: str) -> list[dict[str, Any]]:
    """Minimal cluster-wide RBAC for Litmus pod-level experiments.

    Production deployments should narrow this; for ephemeral CI clusters
    that are destroyed minutes later, this is acceptable.
    """
    role_name = f"{sa}-role"
    binding_name = f"{sa}-binding"
    return [
        {
            "apiVersion": "v1",
            "kind": "ServiceAccount",
            "metadata": {"name": sa, "namespace": namespace},
        },
        {
            "apiVersion": "rbac.authorization.k8s.io/v1",
            "kind": "ClusterRole",
            "metadata": {"name": role_name},
            "rules": [
                {
                    "apiGroups": [""],
                    "resources": [
                        "pods",
                        "pods/log",
                        "events",
                        "services",
                        "configmaps",
                        "secrets",
                    ],
                    "verbs": [
                        "create",
                        "list",
                        "get",
                        "patch",
                        "update",
                        "delete",
                        "deletecollection",
                    ],
                },
                {
                    "apiGroups": [""],
                    "resources": ["pods/exec"],
                    "verbs": ["create"],
                },
                {
                    "apiGroups": ["apps"],
                    "resources": ["deployments", "statefulsets", "daemonsets", "replicasets"],
                    "verbs": ["list", "get", "patch", "update"],
                },
                {
                    "apiGroups": ["batch"],
                    "resources": ["jobs"],
                    "verbs": ["create", "list", "get", "delete", "deletecollection"],
                },
                {
                    "apiGroups": ["litmuschaos.io"],
                    "resources": ["chaosengines", "chaosexperiments", "chaosresults"],
                    "verbs": ["create", "list", "get", "patch", "update", "delete"],
                },
            ],
        },
        {
            "apiVersion": "rbac.authorization.k8s.io/v1",
            "kind": "ClusterRoleBinding",
            "metadata": {"name": binding_name},
            "roleRef": {
                "apiGroup": "rbac.authorization.k8s.io",
                "kind": "ClusterRole",
                "name": role_name,
            },
            "subjects": [
                {"kind": "ServiceAccount", "name": sa, "namespace": namespace},
            ],
        },
    ]


def _experiment_job(
    *,
    name: str,
    namespace: str,
    sa: str,
    image: str,
    experiment: str,
    params: dict[str, str],
    label_key: str,
    label_value: str,
) -> dict[str, Any]:
    env = [{"name": k, "value": v} for k, v in params.items()]
    env.extend(
        [
            {"name": "EXPERIMENT_NAME", "value": experiment},
            {"name": "APP_LABEL_KEY", "value": label_key},
            {"name": "APP_LABEL_VALUE", "value": label_value},
        ]
    )
    return {
        "apiVersion": "batch/v1",
        "kind": "Job",
        "metadata": {"name": name, "namespace": namespace},
        "spec": {
            "backoffLimit": 0,
            "template": {
                "metadata": {"labels": {"app": "ccr-litmus", "experiment": experiment}},
                "spec": {
                    "serviceAccountName": sa,
                    "restartPolicy": "Never",
                    "containers": [
                        {
                            "name": "experiment",
                            "image": image,
                            "imagePullPolicy": "IfNotPresent",
                            "args": ["-name", experiment],
                            "command": ["/experiments"],
                            "env": env,
                        }
                    ],
                },
            },
        },
    }
