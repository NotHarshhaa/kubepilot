# KubePilot

> AI-powered Kubernetes troubleshooting and operations agent.

KubePilot is an open-source AI agent that helps engineers **inspect, troubleshoot, diagnose, and operate Kubernetes workloads** using natural language — instead of manually jumping between `kubectl`, events, logs, metrics, and CI/CD logs.

## Table of Contents

- [What is KubePilot?](#what-is-kubepilot)
- [Core Capabilities](#core-capabilities)
- [Safety Model](#safety-model)
- [Technology Stack](#technology-stack)
- [Project Structure](#project-structure)
- [Quick Start](#quick-start)
- [MVP Scope](#mvp-scope)
- [Design Philosophy](#design-philosophy)
- [Contributing](#contributing)
- [License](#license)

---

## What is KubePilot?

KubePilot connects an AI agent to your cluster and observability stack through a controlled tool layer:

```text
Developer ── natural language ──▶ KubePilot Agent
                                        │
                    ┌───────────────────┼───────────────────┐
                    ▼                   ▼                   ▼
              Kubernetes Tools    Prometheus Tools      Loki Tools
                    │                   │                   │
                    └───────────────────┼───────────────────┘
                                        ▼
                                  AI Reasoning
                                        │
                          ┌─────────────┴─────────────┐
                          ▼                            ▼
                     Diagnosis                  Recommendation
                          │                            │
                          └─────────────┬──────────────┘
                                        ▼
                                 Human Approval
                                        │
                                        ▼
                                  Remediation
                                        │
                                        ▼
                                  Verification
```

The agent does not get unrestricted shell access — it only sees what's exposed through explicit tools (below), and any action beyond a read gets approval before it runs.

## Core Capabilities

**Kubernetes investigation** — pods, deployments, services, nodes, events, container status, logs, resource usage, rollout status. Ask things like *"why is checkout-api restarting?"* or *"why is my deployment stuck?"*

**Observability correlation** — pulls from Prometheus, Loki, and Kubernetes events together, rather than looking at one signal in isolation, to connect a metric spike to the logs and events around it.

**Root-cause analysis** — combines evidence from multiple sources before proposing a cause, e.g. tying a rise in 5xx errors and pod restarts back to a specific recent deployment and a database connection pool issue.

**CI/CD investigation** — reads GitHub Actions workflow logs, identifies the failed step, and correlates it with the triggering commit.

**Controlled remediation** — the agent can *propose* actions (restart, scale, rollback); it does not execute destructive operations on its own.

### Tool layer

```text
Kubernetes: get_pods · get_deployments · get_services · get_events
            get_logs · describe_resource · rollout_status · restart_deployment
Prometheus: query · query_range
Loki:       query_logs
GitHub:     get_repository · get_workflow · get_workflow_logs · get_commit
```

## Safety Model

| Category | Examples | Execution |
|---|---|---|
| Read | get pods, get logs, get events, query metrics | Automatic |
| Operational | restart, scale, rollback | Requires approval |
| Destructive | delete namespace/deployment, modify RBAC | Requires explicit human approval, never automatic |

## Technology Stack

| Component | Technology |
|---|---|
| Language | Python |
| API | FastAPI |
| Agent framework | LangGraph |
| LLM | Gemini / OpenAI / Ollama (provider-agnostic) |
| Kubernetes access | Kubernetes API / kubectl |
| Metrics | Prometheus |
| Logs | Loki |
| CI/CD | GitHub Actions |
| Package management | uv / pip |
| Testing | pytest |

## Project Structure

```text
kubepilot/
├── app/
│   ├── agent/
│   ├── tools/
│   │   ├── kubernetes/
│   │   ├── prometheus/
│   │   ├── loki/
│   │   └── github/
│   ├── api/
│   ├── config/
│   └── main.py
├── tests/
├── deploy/
│   ├── kubernetes/
│   └── helm/
├── examples/
├── docs/
├── .github/workflows/
├── Dockerfile
├── docker-compose.yml
├── pyproject.toml
├── Makefile
├── LICENSE
└── README.md
```

## Quick Start

### Requirements

- Python 3.12+
- Docker
- kubectl
- kind or minikube
- Optional: Ollama, Prometheus, Loki (for local testing)

### Install

```bash
git clone https://github.com/NotHarshhaa/kubepilot.git
cd kubepilot
uv sync   # or: pip install -e .
```

### Configure

Create a `.env` file in the project root:

```env
LLM_PROVIDER=ollama
MODEL_NAME=your-model

KUBECONFIG=~/.kube/config
PROMETHEUS_URL=http://localhost:9090
LOKI_URL=http://localhost:3100
```

### Run

```bash
python -m app.main
```

Then ask:

```text
Why is my nginx pod restarting?
```

KubePilot will inspect the pod and container status, read logs, check recent events and resource usage, query metrics, and return a probable cause with a suggested fix.

## MVP Scope

The first release is intentionally narrow:

- [ ] Pod / deployment / service inspection
- [ ] Kubernetes events and pod logs
- [ ] Rollout status
- [ ] Prometheus and Loki queries with basic metric/log correlation
- [ ] Natural-language interface with tool calling
- [ ] Root-cause analysis and remediation recommendations
- [ ] GitHub Actions workflow inspection and failure analysis
- [ ] Action approval flow, tool permissions, read-only mode, audit logs

Everything else — MCP, RAG, incident memory, Helm/Argo CD integration, multi-agent workflows — is a **later** consideration, only if the core proves useful. It's explicitly out of scope for v0.1.

## Design Philosophy

1. **Start small.** A working single-agent tool beats an unfinished platform.
2. **Tools, not shell access.** The agent interacts with infrastructure only through explicit, permissioned tools.
3. **Evidence before conclusions.** Logs, metrics, and events are inspected before a diagnosis is produced.
4. **Recommend, then approve.** Production changes go through a human, always.
5. **Verify every action.** After remediation, the agent checks that the fix actually worked.
6. **Model-independent.** The infrastructure layer doesn't lock in a single LLM provider.

## Contributing

Contributions are welcome, especially around: Kubernetes/Prometheus/Loki/GitHub tool integrations, new LLM providers, agent workflow logic, tests, and documentation.

## License

Apache License 2.0
