FROM python:3.12-slim

# System deps: curl/ca-certificates for the docker-CLI download, Node/npm to
# install the Claude Code CLI itself (the chat agent's whole runtime —
# without this the backend can create/manage labs fine but every chat turn
# fails with "Claude Code CLI niet gevonden") AND to run stdio MCP servers
# from the catalog. Node comes from NodeSource, NOT `apt-get install nodejs`:
# Debian bookworm's packaged Node was 20.19.2 here, and
# `@microsoft/fabric-mcp` (like several other current MCP packages) declares
# `engines.node >=22` — under Node 20 the process dies immediately on start,
# which the mcp SDK's stdio_client surfaces as an opaque "unhandled errors in
# a TaskGroup" with no hint that the real cause is the Node version.
RUN apt-get update -qq \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \
       curl ca-certificates gnupg \
    && curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq nodejs \
    && npm install -g @anthropic-ai/claude-code \
    && rm -rf /var/lib/apt/lists/*

# docker-CLI as a static binary (NOT docker.io — that pulls in the daemon).
# Pinned version; override DOCKER_CLI_VERSION at build time to bump it.
# Arch comes from `dpkg --print-architecture` (the actual base-image arch),
# NOT the `TARGETARCH` build-arg: BuildKit only populates that correctly when
# the build request carries an explicit --platform, which `docker compose
# build/up` doesn't always set (it defaulted to amd64 on this arm64 host).
# docker's static-binary directory names (aarch64/x86_64) differ from
# Debian's (arm64/amd64), so map explicitly rather than reusing either as-is.
ARG DOCKER_CLI_VERSION=27.3.1
RUN set -eu; \
    case "$(dpkg --print-architecture)" in \
      amd64) arch=x86_64 ;; \
      arm64) arch=aarch64 ;; \
      *) echo "unsupported arch: $(dpkg --print-architecture)" >&2; exit 1 ;; \
    esac; \
    curl -fsSL "https://download.docker.com/linux/static/stable/${arch}/docker-${DOCKER_CLI_VERSION}.tgz" \
      -o /tmp/docker.tgz \
    && tar -xzf /tmp/docker.tgz -C /tmp docker/docker \
    && mv /tmp/docker/docker /usr/local/bin/docker \
    && chmod +x /usr/local/bin/docker \
    && rm -rf /tmp/docker.tgz /tmp/docker

# Several Microsoft MCP packages (Fabric MCP confirmed; Azure MCP/Dev Box are
# the same family) ship a .NET binary behind their npm/npx wrapper, not a
# plain Node script. .NET's globalization layer needs ICU, which this slim
# Python/Debian image doesn't have — without this the process FailFasts on
# startup ("Couldn't find a valid ICU package... libicu"), which the mcp
# SDK's stdio_client only reports as an opaque "unhandled errors in a
# TaskGroup". Invariant mode sidesteps the ICU dependency entirely (no
# culture-aware formatting needed for an MCP tool subprocess) instead of
# pinning a Debian libicu<NN> package name that drifts across base-image
# releases (this image moved from bookworm to trixie under us already).
ENV DOTNET_SYSTEM_GLOBALIZATION_INVARIANT=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    # `uv`/`uvx` for the MCP catalog's Python-based entries (Fabric RTI,
    # Markitdown) — same pattern as node/npx for the JS-based ones.
    && pip install --no-cache-dir uv \
    # The `az` CLI itself — not just its config files. Azure MCP Server and
    # Fabric MCP's .NET credential chain (AzureCliCredential, one of several
    # DefaultAzureCredential fallbacks) shells out to the `az` binary to read
    # an msal_bundle profile's materialized AZURE_CONFIG_DIR session; without
    # the binary on PATH it fails with "Azure CLI not installed" even when a
    # valid cached session is sitting right there in that directory. `pip
    # install azure-cli` (Microsoft's own documented install method) instead
    # of the apt repo: the apt repo only ships builds for specific Debian
    # codenames and lags behind — this image already drifted from bookworm to
    # trixie under us once (see the ICU note above), and pip works regardless.
    && pip install --no-cache-dir azure-cli

COPY src/ ./src/

ENV PYTHONPATH=/app/src
ENV LABX_HOME=/data

EXPOSE 8090

CMD ["python", "-m", "uvicorn", "server:app", "--app-dir", "src", "--host", "0.0.0.0", "--port", "8090"]
