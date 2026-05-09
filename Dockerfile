# chaos-ci-runner image: Python CLI + kubectl + helm + k3d.
#
# k3d itself uses the host's Docker daemon to create the ephemeral
# cluster. To use this image in CI, mount the Docker socket from the
# runner: -v /var/run/docker.sock:/var/run/docker.sock
#
# Versions are pinned for reproducibility; bump them in PRs.
FROM python:3.11-slim AS base

ARG KUBECTL_VERSION=v1.30.4
ARG HELM_VERSION=v3.15.4
ARG K3D_VERSION=v5.7.4

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        bash \
        git \
    && rm -rf /var/lib/apt/lists/*

RUN set -eux; \
    arch="$(dpkg --print-architecture)"; \
    case "$arch" in \
        amd64) kbarch="amd64"; helmarch="amd64"; k3darch="amd64" ;; \
        arm64) kbarch="arm64"; helmarch="arm64"; k3darch="arm64" ;; \
        *) echo "unsupported arch: $arch" >&2; exit 1 ;; \
    esac; \
    curl -fsSL -o /usr/local/bin/kubectl \
        "https://dl.k8s.io/release/${KUBECTL_VERSION}/bin/linux/${kbarch}/kubectl"; \
    chmod +x /usr/local/bin/kubectl; \
    curl -fsSL -o /tmp/helm.tar.gz \
        "https://get.helm.sh/helm-${HELM_VERSION}-linux-${helmarch}.tar.gz"; \
    tar -xzf /tmp/helm.tar.gz -C /tmp; \
    mv "/tmp/linux-${helmarch}/helm" /usr/local/bin/helm; \
    chmod +x /usr/local/bin/helm; \
    rm -rf /tmp/helm.tar.gz "/tmp/linux-${helmarch}"; \
    curl -fsSL -o /usr/local/bin/k3d \
        "https://github.com/k3d-io/k3d/releases/download/${K3D_VERSION}/k3d-linux-${k3darch}"; \
    chmod +x /usr/local/bin/k3d

WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY chaos_ci_runner ./chaos_ci_runner
RUN pip install .

WORKDIR /work
ENTRYPOINT ["chaos-ci-runner"]
CMD ["--help"]
