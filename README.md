# KubePilot

> AI-powered Kubernetes troubleshooting and operations agent.

KubePilot is an open-source AI agent that helps engineers **inspect, troubleshoot, diagnose, and operate Kubernetes workloads** using natural language — instead of manually jumping between `kubectl`, events, logs, metrics, and CI/CD logs.

---

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

---

## Core Capabilities

**Kubernetes investigation** — pods, deployments, services, nodes, events, container status, logs, resource usage, rollout status. Ask things like *"why is checkout-api restarting?"* or *"why is my deployment stuck?"*

**Observability correlation** — pulls from Prometheus, Loki, and Kubernetes events together, rather than looking at one signal in isolation, to connect a metric spike to the logs and events around it.

**Root-cause analysis** — combines evidence from multiple sources before proposing a cause, e.g. tying a rise in 5xx errors and pod restarts back to a specific recent deployment and a database connection pool issue.

**CI/CD investigation** — reads GitHub Actions workflow logs, identifies the failed step, and correlates it with the triggering commit.

**Controlled remediation** — the agent can *propose* actions (restart, scale, rollback); it does not execute destructive operations on its own.

### Tool layer

```text
Kubernetes:  get_pods · get_deployments · get_services · get_events
             get_logs (supports previous crash logs) · describe_resource
             rollout_status · get_ingresses · get_pvc_status · get_hpa
             cluster_health_scan · restart_deployment · scale_deployment · rollback_deployment
GitOps/Helm: helm_list · helm_status · helm_history · rollback_helm_release
             get_argocd_apps · get_argocd_app_diff · sync_argocd_app
Diagnostics: run_diagnostic_runbook (CrashLoopBackOff · OOMKilled · ImagePullBackOff · PendingPod)
Advisor:     analyze_resource_optimization (CPU/Memory right-sizing & cloud cost savings)
Verify:      verify_remediation (automated post-action rollout & crash verification)
Prometheus:  query · query_range
Loki:        query_logs
GitHub:      get_repository · get_workflow · get_workflow_logs · get_commit
Protocol:    MCP (Model Context Protocol) Server for Cursor, Claude Desktop & Zed
```

---

## Safety Model

| Category | Examples | Execution |
|---|---|---|
| Read | get pods, get logs, get events, query metrics, cluster scan | Automatic |
| Operational | restart, scale, rollback | Requires approval |
| Destructive | delete namespace/deployment, modify RBAC | Requires explicit human approval, never automatic |

---

## Technology Stack

| Component | Technology |
|---|---|
| Language | Python |
| API | FastAPI (REST + SSE Streaming) |
| CLI | Rich Terminal UI |
| Agent framework | LangGraph (ReAct loop + Session Memory) |
| LLM | Anthropic Claude / Gemini / OpenAI / Ollama (provider-agnostic) |
| Kubernetes access | Kubernetes API / kubectl |
| Metrics | Prometheus |
| Logs | Loki |
| CI/CD | GitHub Actions |
| Alerting & Webhooks | Slack / Discord / Generic HTTP Webhooks |
| Package management | uv / pip |
| Testing | pytest |

---

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
MODEL_NAME=llama3.1

KUBECONFIG=~/.kube/config
PROMETHEUS_URL=http://localhost:9090
LOKI_URL=http://localhost:3100
```

### Running KubePilot

#### 1. Interactive CLI

Launch the interactive troubleshooting shell:

```bash
uv run kubepilot
# or
uv run kubepilot chat
```

Available CLI commands:
- `kubepilot ask "Why is nginx pod restarting?"` — Run a one-shot troubleshooting query
- `kubepilot scan` — Instant cluster-wide health triage across all nodes, pods, and deployments
- `kubepilot runbook [pod] [--type crashloop|oom|imagepull|pending]` — Run automated deep diagnostic runbook
- `kubepilot advisor [--namespace <ns>] [--workload <name>]` — AI Resource & Cost Optimization Advisor
- `kubepilot verify <target> [--namespace <ns>]` — Post-remediation rollout & health verification
- `kubepilot helm [--namespace <ns>]` — Inspect Helm releases and revisions
- `kubepilot argocd [--app <name>]` — Inspect Argo CD GitOps applications and sync status
- `kubepilot mcp` — Launch Model Context Protocol (MCP) server over stdio
- `kubepilot approvals list` — View pending and executed operational actions
- `kubepilot approvals approve <id>` — Approve and execute a proposed operational action
- `kubepilot approvals deny <id>` — Deny a proposed operational action
- `kubepilot report` — Generate an incident post-mortem markdown report

#### 2. REST API & SSE Streaming

Start the FastAPI server:

```bash
python -m app.main
```

Then query the API:

```bash
# Natural-language query with session memory
curl -X POST http://localhost:8000/query \
  -H "Content-Type: application/json" \
  -d '{"question": "Why is my nginx pod restarting?", "session_id": "sess-1"}'

# Real-time SSE streaming (tool execution events & reasoning)
curl -N -X POST http://localhost:8000/query/stream \
  -H "Content-Type: application/json" \
  -d '{"question": "Run health check on checkout service", "session_id": "sess-1"}'

# Instant cluster-wide health triage
curl http://localhost:8000/cluster/scan

# Run automated diagnostic runbook for a pod
curl -X POST http://localhost:8000/diagnostics/runbook \
  -H "Content-Type: application/json" \
  -d '{"pod_name": "checkout-api-xxx", "namespace": "prod", "runbook_type": "auto"}'

# AI Resource & Cost Optimization Advisory
curl http://localhost:8000/advisor/resources?namespace=default

# Post-remediation rollout & health verification
curl -X POST http://localhost:8000/remediation/verify \
  -H "Content-Type: application/json" \
  -d '{"target": "checkout-api", "namespace": "prod", "action_type": "restart"}'

# Inspect Helm releases
curl http://localhost:8000/gitops/helm?namespace=kube-system

# Inspect Argo CD applications
curl http://localhost:8000/gitops/argocd?namespace=argocd

# Generate post-mortem report
curl -X POST http://localhost:8000/reports/incident \
  -H "Content-Type: application/json" \
  -d '{"title": "API Gateway Outage", "summary": "CrashLoopBackOff due to OOM", "root_cause": "Memory leak"}'
```

#### 3. Model Context Protocol (MCP) Server

KubePilot includes a built-in MCP server that exposes all Kubernetes, GitOps, and diagnostic tools to any MCP-compliant AI client, such as **Claude Desktop**, **Cursor**, **Zed**, or **Windsurf**.

Launch standalone:
```bash
uv run kubepilot mcp
# or:
python -m app.mcp
```

##### Claude Desktop Configuration (`claude_desktop_config.json`)
```json
{
  "mcpServers": {
    "kubepilot": {
      "command": "uv",
      "args": ["--directory", "/path/to/kubepilot", "run", "kubepilot", "mcp"]
    }
  }
}
```

##### Cursor / Windsurf Configuration (`mcp.json`)
```json
{
  "mcpServers": {
    "kubepilot": {
      "command": "python",
      "args": ["-m", "app.mcp"],
      "cwd": "/path/to/kubepilot"
    }
  }
}
```

---

## Core Capabilities

- [x] Pod / deployment / service inspection
- [x] Ingress, PersistentVolumeClaim (PVC), and HorizontalPodAutoscaler (HPA) inspection
- [x] Container logs with crash inspection (`previous=True` for CrashLoopBackOff)
- [x] Automated cluster-wide health triage scanner (`cluster_health_scan`)
- [x] Rollout status and revision history
- [x] **Automated Diagnostic Runbooks** (`CrashLoopBackOff`, `OOMKilled`, `ImagePullBackOff`, `PendingPod`)
- [x] **AI Resource & Cost Optimization Advisor** (CPU/memory requests vs limits vs actual usage, right-sizing & savings)
- [x] **Automated Post-Remediation Verification Engine** (Validates rollout health, crash rates, and events after fixes)
- [x] **GitOps & Package Manager Support** (Helm releases, history, rollback; Argo CD app status, diff, sync)
- [x] **Model Context Protocol (MCP) Server** (standard stdio JSON-RPC for Claude Desktop & Cursor)
- [x] Prometheus and Loki queries with basic metric/log correlation
- [x] Multi-turn conversational sessions and SSE real-time streaming
- [x] Interactive Rich CLI (`kubepilot`) with in-terminal approval prompts and slash commands
- [x] SRE Incident Post-Mortem Markdown Report Generator
- [x] Action approval flow, tool permissions, read-only mode, audit logs
- [x] Webhook alerting for approvals (Slack, Discord, generic HTTP)
- [x] Multi-provider LLM support: Anthropic Claude, Google Gemini, OpenAI, Ollama

---

## Design Philosophy

1. **Start small.** A working single-agent tool beats an unfinished platform.
2. **Tools, not shell access.** The agent interacts with infrastructure only through explicit, permissioned tools.
3. **Evidence before conclusions.** Logs, metrics, and events are inspected before a diagnosis is produced.
4. **Recommend, then approve.** Production changes go through a human, always.
5. **Verify every action.** After remediation, the agent checks that the fix actually worked.
6. **Model-independent.** The infrastructure layer doesn't lock in a single LLM provider.

---

## Contributing

Contributions are welcome, especially around: Kubernetes/Prometheus/Loki/GitHub tool integrations, new LLM providers, agent workflow logic, tests, and documentation.

---

## Author & Community

Built with passion and purpose by [**Harshhaa**](https://github.com/NotHarshhaa).  
Your ideas, feedback, and contributions are what make this project better.

Let’s shape the future of AIOps together! 🚀

**Connect & Collaborate:**  

* **GitHub:** [@NotHarshhaa](https://github.com/NotHarshhaa)  
* **Blog:** [HARSHHAA](https://blog.harshhaareddy.com)  
* **Telegram Community:** [Join Here](https://t.me/prodevopsguy)  
* **LinkedIn:** [Harshhaa Vardhan Reddy](https://www.linkedin.com/in/NotHarshhaa/)  

---

## How You Can Support

If you found this project useful:  

* **Star** the repository to show your support  
* **Share** it with your friends and colleagues  
* **Open issues** or **submit pull requests** to help improve it

---

### Stay Connected

[![Follow Me](https://imgur.com/2j7GSPs.png)](https://github.com/NotHarshhaa)

Join the community, share your experience, and help us grow!
