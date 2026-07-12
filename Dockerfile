ARG KUBECTL_VERSION=v1.32.2
ARG K6_VERSION=v1.2.0

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
ARG TARGETARCH
ARG KUBECTL_VERSION
ARG K6_VERSION
RUN apt-get update \
    && apt-get install --yes --no-install-recommends ca-certificates curl \
    && curl --fail --location --retry 3 \
      "https://dl.k8s.io/release/${KUBECTL_VERSION}/bin/linux/${TARGETARCH}/kubectl" \
      --output /usr/local/bin/kubectl \
    && chmod 0755 /usr/local/bin/kubectl \
    && curl --fail --location --retry 3 \
      "https://github.com/grafana/k6/releases/download/${K6_VERSION}/k6-${K6_VERSION}-linux-${TARGETARCH}.tar.gz" \
      --output /tmp/k6.tar.gz \
    && tar -xzf /tmp/k6.tar.gz --strip-components=1 -C /usr/local/bin \
      "k6-${K6_VERSION}-linux-${TARGETARCH}/k6" \
    && rm -rf /var/lib/apt/lists/* /tmp/k6.tar.gz \
    && kubectl version --client \
    && k6 version
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
