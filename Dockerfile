# syntax=docker/dockerfile:1
FROM python:3.11-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    iproute2 \
    iptables \
    conntrack \
    ethtool \
    iputils-ping \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install agent Python deps
COPY agent/pyproject.toml agent/ /app/agent/
RUN pip install --upgrade pip && \
    pip install -e "agent[dev]"

# Install CLI (npm) deps
COPY cli/package.json cli/ /app/cli/
WORKDIR /app/cli
RUN npm install --omit=dev

WORKDIR /app

# Copy the rest
COPY . /app

# Non-root user for the web/API parts; enforcerd needs CAP_NET_ADMIN
RUN groupadd -r ebagent && useradd -r -g ebagent ebagent
RUN mkdir -p /run /var/lib/eb-agent /etc/eb-agent /var/log/eb-agent && \
    chown -R ebagent:ebagent /var/lib/eb-agent /var/log/eb-agent

USER ebagent

EXPOSE 8080

ENV EB_MODE=lab \
    EB_ENFORCERD_SOCKET=/run/eb-enforcerd.sock \
    EB_DATA_DIR=/var/lib/eb-agent

CMD ["python3", "-m", "eb_agent.core"]
