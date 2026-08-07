ARG GO_VERSION=1.26.5
ARG KUBECTL_VERSION=v1.36.2
ARG KUBECTL_MODULE_VERSION=v0.36.2

FROM golang:${GO_VERSION}-bookworm AS tool-builder
ARG KUBECTL_VERSION
ARG KUBECTL_MODULE_VERSION
WORKDIR /src/kubectl
COPY tools/kubectl ./
RUN test "$(go list -m -f '{{.Version}}' k8s.io/kubectl)" = "${KUBECTL_MODULE_VERSION}" \
    && CGO_ENABLED=0 go build -trimpath \
      -ldflags="-s -w -X k8s.io/component-base/version.gitVersion=${KUBECTL_VERSION} -X k8s.io/component-base/version.gitTreeState=clean" \
      -o /out/kubectl .
WORKDIR /src/k6
COPY tools/k6 ./
RUN CGO_ENABLED=0 go build -trimpath -ldflags="-s -w" -o /out/k6 .

FROM python:3.13-slim AS builder
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
COPY chamber ./chamber
RUN python -m pip install --no-cache-dir uv==0.11.26 \
    && UV_PROJECT_ENVIRONMENT=/opt/venv uv sync \
      --frozen \
      --no-dev \
      --extra ui \
      --no-editable

FROM python:3.13-slim AS runtime
RUN apt-get update \
    && apt-get upgrade --yes \
    && apt-get install --yes --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*
COPY --from=tool-builder /out/kubectl /out/k6 /usr/local/bin/
RUN kubectl version --client && k6 version
RUN groupadd --gid 10001 ampule \
    && useradd --uid 10001 --gid 10001 --create-home --home-dir /home/ampule ampule \
    && mkdir -p /data/.chamber /workspace \
    && chown -R ampule:ampule /data /workspace
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    AMPULE_CHAMBER_WORKSPACE=/data/.chamber
WORKDIR /workspace
USER 10001:10001
EXPOSE 8765
ENTRYPOINT ["ampule-chamber"]
CMD ["ui", "--host", "0.0.0.0", "--port", "8765", "--workspace", "/data/.chamber", "--no-open", "--allow-remote"]
