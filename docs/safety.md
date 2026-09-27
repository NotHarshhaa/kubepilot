# Safety Model

KubePilot is built on one principle: **recommend, then approve**. The agent sees only the
explicitly registered tools, and every tool declares its blast radius up front.

## Categories

| Category | Examples | Execution |
|---|---|---|
| **Read** | get pods / logs / events, query Prometheus or Loki, read CI logs | Automatic — these cannot change cluster state |
| **Operational** | `restart_deployment`, `scale_deployment`, `rollback_deployment` | Requires approval via the API; a human sees tool + args before it runs |
| **Destructive** | delete namespace/deployment, modify RBAC | Requires explicit human approval, never automatic — **not implemented in v0.1 and never registered**, so the agent cannot call them even by hallucinating a tool name |

## Enforcement points

1. **Registration** (`app/tools/registry.py`) — tools are registered with a category; there is no
   path for the agent to call a function that is not registered.
2. **Guard** (`app/tools/approvals.py:guarded_tool`) — operational tools are wrapped before being
   bound to the LLM. The wrapper either blocks (read-only mode), converts the call into a pending
   approval, or (if approval is disabled by config) executes and audits.
3. **Approval API** (`POST /approvals/{id}/approve|deny`) — the only way a pending action executes.
4. **Audit log** (`AUDIT_LOG_PATH`, default `logs/audit.jsonl`) — records proposals, approvals,
   denials, executions, and read-only blocks with arguments and timestamps.
5. **Read-only mode** (`READ_ONLY=true`) — operational tools return a block message; useful for
   pointing KubePilot at production for diagnosis only.

## Threat notes

- The LLM cannot escalate: operational actions require a human API call that carries no LLM input.
- The kubeconfig mounted for KubePilot should itself be least-privileged (e.g. get/list/watch plus
  patch on deployments only) — the tool layer is a boundary, not a substitute for RBAC.
- Audit log contents are append-only within the process; ship them to your log platform for
  retention.
