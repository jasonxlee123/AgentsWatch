FROM node:22-alpine AS frontend-build

WORKDIR /build/frontend

COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

COPY frontend/index.html frontend/vite.config.mjs ./
COPY frontend/src ./src

ARG AGENTSWATCH_FRONTEND_BASE=/agentswatch/
ENV AGENTSWATCH_FRONTEND_BASE=${AGENTSWATCH_FRONTEND_BASE}
RUN npm run build


FROM python:3.12-slim AS python-dependencies

ENV VIRTUAL_ENV=/opt/agentswatch-venv
RUN python -m venv "${VIRTUAL_ENV}"
ENV PATH="${VIRTUAL_ENV}/bin:${PATH}"

COPY requirements-api.txt requirements-mcp.txt ./
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements-api.txt -r requirements-mcp.txt


FROM python:3.12-slim AS management-api

ARG AGENTSWATCH_UID=10001
ARG AGENTSWATCH_GID=10001

RUN apt-get update \
    && apt-get install --no-install-recommends -y ca-certificates openssh-client sshpass tini \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid "${AGENTSWATCH_GID}" agentswatch \
    && useradd --uid "${AGENTSWATCH_UID}" --gid "${AGENTSWATCH_GID}" \
        --home-dir /data/agentswatch/management/home --no-create-home \
        --shell /usr/sbin/nologin agentswatch

ENV VIRTUAL_ENV=/opt/agentswatch-venv \
    PATH="/opt/agentswatch-venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    HOME=/data/agentswatch/management/home \
    AGENTSWATCH_AUTOMATION_DATA_ROOT=/data/agentswatch/management/automation \
    AGENTSWATCH_STATIC_DIR=/opt/agentswatch/frontend-dist

WORKDIR /opt/agentswatch

COPY --from=python-dependencies /opt/agentswatch-venv /opt/agentswatch-venv
COPY --chown=agentswatch:agentswatch agentswatch_api ./agentswatch_api
COPY --chown=agentswatch:agentswatch agentswatch_automation ./agentswatch_automation
COPY --chown=agentswatch:agentswatch agentswatch_mcp ./agentswatch_mcp

# Only immutable, reviewed automation sources enter the image. Local inventory,
# runner workspaces, task artifacts and managed drafts are deliberately excluded.
COPY --chown=agentswatch:agentswatch automation/catalog.json automation/ansible.cfg ./automation/
COPY --chown=agentswatch:agentswatch automation/playbooks ./automation/playbooks
COPY --chown=agentswatch:agentswatch automation/roles ./automation/roles

COPY --from=frontend-build --chown=agentswatch:agentswatch \
    /build/frontend/dist /opt/agentswatch/frontend-dist
COPY --chown=agentswatch:agentswatch deploy/management/container-entrypoint.sh \
    /usr/local/bin/agentswatch-management-entrypoint
COPY --chown=agentswatch:agentswatch deploy/management/api_socket_launcher.py \
    /usr/local/bin/agentswatch-api-socket-launcher
COPY --chown=agentswatch:agentswatch deploy/management/mcp_socket_launcher.py \
    /usr/local/bin/agentswatch-mcp-socket-launcher
COPY --chown=agentswatch:agentswatch deploy/management/openclaw_loopback_relay.py \
    /usr/local/bin/agentswatch-openclaw-loopback-relay
COPY --chown=agentswatch:agentswatch scripts/manage_auth_user.py \
    /usr/local/bin/agentswatch-manage-auth-user

RUN chmod 0755 \
        /usr/local/bin/agentswatch-management-entrypoint \
        /usr/local/bin/agentswatch-api-socket-launcher \
        /usr/local/bin/agentswatch-mcp-socket-launcher \
        /usr/local/bin/agentswatch-openclaw-loopback-relay \
        /usr/local/bin/agentswatch-manage-auth-user \
    && find /opt/agentswatch -type d -exec chmod 0755 {} + \
    && find /opt/agentswatch -type f -exec chmod 0644 {} +

USER agentswatch:agentswatch

HEALTHCHECK --interval=15s --timeout=3s --start-period=20s --retries=4 \
    CMD python -c "import socket; s=socket.socket(socket.AF_UNIX); s.settimeout(2); s.connect('/data/agentswatch/management/run/api.sock'); s.sendall(b'GET /healthz HTTP/1.0\r\nHost: localhost\r\n\r\n'); response=s.recv(256); s.close(); assert b' 200 ' in response.split(b'\r\n', 1)[0]"

ENTRYPOINT ["tini", "--", "/usr/local/bin/agentswatch-management-entrypoint"]

# Automation recovery uses in-process worker threads. Multiple Uvicorn workers
# would race to recover the same queue, so the management API is intentionally
# kept as one worker until execution is moved to an external worker service.
CMD ["python", "/usr/local/bin/agentswatch-api-socket-launcher"]
