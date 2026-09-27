# Example questions KubePilot is built to answer

Post these to `POST /query` (or just run the API and try them):

## Kubernetes investigation
- "Why is my nginx pod restarting?"
- "Why is checkout-api stuck at 2/5 replicas?"
- "What changed in the default namespace in the last hour?"
- "Show me the pods that are not ready right now."
- "Which pods are using the most CPU in production?"

## Observability correlation
- "Did error rates spike for payments-api in the last hour? If so, what do the logs say?"
- "Are there any 5xx spikes in the last 24 hours for the frontend service?"
- "Search Loki for exceptions from the orders service in the last 30 minutes."

## CI/CD investigation
- "Why did the last CI run for NotHarshhaa/kubepilot fail?" (uses `get_workflow_jobs` + `get_workflow_logs`)
- "What changed in commit <sha> that broke the build?"

## Remediation (proposed, never auto-executed)
- "Restart the checkout-api deployment." → the agent proposes the action and returns an approval ID;
  approve with `POST /approvals/{id}/approve`, deny with `POST /approvals/{id}/deny`.
- "Scale payments-api to 5 replicas."
- "Roll back the frontend deployment to the previous revision."

## Local test cluster
```bash
kind create cluster --name kubepilot-dev
kubectl create deployment crashy --image=nginx --replicas=2
kubectl run bad --image=nginx-invalid-tag   # ImagePullBackOff to investigate
python -m app.main
```
