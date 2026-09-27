# Architecture

KubePilot is a single-agent tool-calling system. There is no autonomous shell access and no
background side effects: every capability is an explicitly registered tool with a declared
safety category.

```text
Developer ── natural language ──▶ KubePilot Agent (LangGraph ReAct loop)
                                        │
                    ┌───────────────────┼───────────────────┐
                    ▼                   ▼                   ▼
              Kubernetes Tools    Prometheus/Loki      GitHub Actions
              (kubernetes client) (HTTP APIs)          (REST API)
                    │                   │                   │
                    └───────────────────┴───────────────────┘
                                        ▼
                          Evidence → Probable cause → Recommendation
                                        ▼
                     Operational actions go through Human Approval
                                        ▼
                                  Execution + Verification
```

## Components

| Component | Location | Responsibility |
|---|---|---|
| Config | `app/config/settings.py` | Pydantic settings from `.env` / environment |
| Tool registry | `app/tools/registry.py` | Name + description + `SafetyCategory` per tool |
| Approval manager | `app/tools/approvals.py` | Proposal/approve/deny lifecycle for operational tools |
| Audit log | `app/tools/audit.py` | Append-only JSONL record of security-relevant events |
| Kubernetes tools | `app/tools/kubernetes/tools.py` | Pods, deployments, services, events, logs, rollout, restart/scale/rollback |
| Prometheus tools | `app/tools/prometheus/prometheus.py` | Instant + range PromQL queries |
| Loki tool | `app/tools/loki/loki.py` | LogQL range queries |
| GitHub tools | `app/tools/github/github.py` | Repos, Actions runs, jobs, logs, commits |
| Agent | `app/agent/` | Provider-agnostic LLM factory, system prompt, ReAct graph |
| API | `app/api/routes.py` | `/query`, `/approvals`, `/health` |

## Safety model

| Category | Examples | Execution |
|---|---|---|
| `read` | get pods, logs, events, metrics, CI logs | Automatic |
| `operational` | restart, scale, rollback | Approval via `POST /approvals/{id}/approve` |
| `destructive` | delete namespace/deployment, RBAC changes | Never registered — not reachable by the agent |

Operational tools are never handed to the LLM in executable form: `guarded_tool` intercepts the
call and returns a proposal with an approval ID. The action runs only when a human approves it
through the API, and both transitions are written to the audit log. `READ_ONLY=true` blocks
operational tools entirely.

## Flow of a query

1. `POST /query` receives a natural-language question.
2. The LangGraph ReAct agent reasons and calls read tools until it has enough evidence.
3. If remediation is needed, the agent proposes an operational tool; the tool call returns a
   proposal string with an approval ID instead of executing.
4. The agent answers with evidence → probable cause → recommended fix (and the approval ID if any).
5. A human approves via the API; the action executes and its result is returned. The agent's
   instructions require post-action verification with read tools.
