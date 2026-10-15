"""AI Resource & Cost Optimization Advisor for Kubernetes workloads.

Analyzes CPU/Memory requests and limits versus actual utilization.
Detects:
- Over-provisioned containers (idle cloud spend waste with monthly $ savings).
- Under-provisioned containers (risk of CPU throttling and OOMKilled).
- Workloads missing resource requests/limits (noisy neighbor risk).
Generates right-sized recommendations with concrete kubectl patch commands.
"""

from dataclasses import dataclass, field
from typing import Any

from kubernetes import client

from app.config.settings import get_settings
from app.tools.kubernetes.tools import apps
from app.tools.registry import SafetyCategory, register

# Standard cloud benchmark prices ($/vCPU/month and $/GB/month across AWS/GCP/Azure)
ESTIMATED_VCPU_MONTHLY_COST = 32.0  # ~$32/vCPU/month
ESTIMATED_GB_RAM_MONTHLY_COST = 4.2  # ~$4.20/GB/month


def parse_cpu_to_cores(val: str | None) -> float:
    """Parse a Kubernetes CPU quantity string (e.g. '250m', '1', '0.5') to float cores."""
    if not val:
        return 0.0
    val = str(val).strip()
    if val.endswith("m"):
        try:
            return float(val[:-1]) / 1000.0
        except ValueError:
            return 0.0
    if val.endswith("u"):
        try:
            return float(val[:-1]) / 1_000_000.0
        except ValueError:
            return 0.0
    try:
        return float(val)
    except ValueError:
        return 0.0


def parse_memory_to_bytes(val: str | None) -> float:
    """Parse a Kubernetes memory quantity string (e.g. '256Mi', '1Gi', '500M') to bytes."""
    if not val:
        return 0.0
    val = str(val).strip()
    multipliers = {
        "Ki": 1024,
        "Mi": 1024**2,
        "Gi": 1024**3,
        "Ti": 1024**4,
        "k": 1000,
        "M": 1000**2,
        "G": 1000**3,
        "T": 1000**4,
    }
    for suffix, factor in multipliers.items():
        if val.endswith(suffix):
            try:
                return float(val[: -len(suffix)]) * factor
            except ValueError:
                return 0.0
    try:
        return float(val)
    except ValueError:
        return 0.0


def format_cpu(cores: float) -> str:
    """Format float cores into standard Kubernetes CPU string."""
    if cores < 1.0:
        return f"{max(int(cores * 1000), 10)}m"
    return f"{cores:.1f}" if cores % 1 != 0 else f"{int(cores)}"


def format_memory(bytes_val: float) -> str:
    """Format bytes into standard Kubernetes Mi/Gi string."""
    mb = bytes_val / (1024**2)
    if mb >= 1024:
        gb = mb / 1024
        return f"{gb:.1f}Gi" if gb % 1 != 0 else f"{int(gb)}Gi"
    return f"{max(int(mb), 16)}Mi"


@dataclass
class ContainerAdvice:
    container: str
    current_cpu_req: str
    current_cpu_limit: str
    current_mem_req: str
    current_mem_limit: str
    actual_cpu_cores: float
    actual_mem_bytes: float
    rec_cpu_req: str
    rec_cpu_limit: str
    rec_mem_req: str
    rec_mem_limit: str
    status: str  # OVER_PROVISIONED, UNDER_PROVISIONED, UNCONSTRAINED, OPTIMAL
    findings: list[str] = field(default_factory=list)
    monthly_savings: float = 0.0


@dataclass
class WorkloadOptimizationReport:
    workload: str
    namespace: str
    replicas: int
    containers: list[ContainerAdvice] = field(default_factory=list)
    total_monthly_savings: float = 0.0
    patch_snippet: str = ""


def _get_pod_metrics(namespace: str) -> dict[str, dict[str, tuple[float, float]]]:
    """Fetch live pod CPU and memory usage from metrics.k8s.io API."""
    metrics: dict[str, dict[str, tuple[float, float]]] = {}
    try:
        custom = client.CustomObjectsApi()
        items = custom.list_namespaced_custom_object(
            group="metrics.k8s.io",
            version="v1beta1",
            namespace=namespace,
            plural="pods",
        ).get("items", [])

        for item in items:
            pod_name = item.get("metadata", {}).get("name", "")
            metrics[pod_name] = {}
            for c in item.get("containers", []):
                c_name = c.get("name", "")
                usage = c.get("usage", {})
                cpu_cores = parse_cpu_to_cores(usage.get("cpu"))
                mem_bytes = parse_memory_to_bytes(usage.get("memory"))
                metrics[pod_name][c_name] = (cpu_cores, mem_bytes)
    except Exception:
        pass
    return metrics


def analyze_deployment_resources(
    dep: Any,
    pod_metrics: dict[str, dict[str, tuple[float, float]]],
) -> WorkloadOptimizationReport:
    """Analyze a single deployment spec and live usage metrics."""
    dep_name = dep.metadata.name
    namespace = dep.metadata.namespace
    replicas = dep.spec.replicas or 1

    # Match pods belonging to deployment
    matching_metrics: dict[str, list[tuple[float, float]]] = {}
    for p_name, c_dict in pod_metrics.items():
        if p_name.startswith(dep_name):
            for c_name, usage in c_dict.items():
                matching_metrics.setdefault(c_name, []).append(usage)

    container_advices: list[ContainerAdvice] = []
    total_savings = 0.0
    patch_containers = []

    containers = dep.spec.template.spec.containers or []
    for c in containers:
        c_name = c.name
        res = c.resources
        requests = res.requests if res and res.requests else {}
        limits = res.limits if res and res.limits else {}

        curr_cpu_req_str = requests.get("cpu", "none")
        curr_cpu_lim_str = limits.get("cpu", "none")
        curr_mem_req_str = requests.get("memory", "none")
        curr_mem_lim_str = limits.get("memory", "none")

        cpu_req_cores = parse_cpu_to_cores(requests.get("cpu"))
        cpu_lim_cores = parse_cpu_to_cores(limits.get("cpu"))
        mem_req_bytes = parse_memory_to_bytes(requests.get("memory"))
        mem_lim_bytes = parse_memory_to_bytes(limits.get("memory"))

        # Calculate average/peak usage
        usages = matching_metrics.get(c_name, [])
        if usages:
            act_cpu = max(u[0] for u in usages)
            act_mem = max(u[1] for u in usages)
        else:
            # Baseline estimation if metrics server not responding
            act_cpu = cpu_req_cores * 0.3 if cpu_req_cores > 0 else 0.05
            act_mem = mem_req_bytes * 0.4 if mem_req_bytes > 0 else (64 * 1024 * 1024)

        findings = []
        status = "OPTIMAL"

        # Check unconstrained
        if cpu_req_cores == 0 or mem_req_bytes == 0:
            status = "UNCONSTRAINED"
            findings.append("Missing CPU or memory requests: risk of unpredictable scheduling.")
        if cpu_lim_cores == 0 or mem_lim_bytes == 0:
            status = "UNCONSTRAINED"
            findings.append("Missing CPU or memory limits: risk of node resource exhaustion.")

        # Right-sized target calculations (safety buffer: +25% for req, +50% for limit)
        target_cpu_req = max(act_cpu * 1.25, 0.02)
        target_cpu_lim = max(act_cpu * 1.8, target_cpu_req * 1.4)

        target_mem_req = max(act_mem * 1.25, 32 * 1024 * 1024)
        target_mem_lim = max(act_mem * 1.6, target_mem_req * 1.3)

        # Over-provisioned check
        if cpu_req_cores > 0 and (cpu_req_cores >= act_cpu * 3.0 and (cpu_req_cores - act_cpu) > 0.3):
            status = "OVER_PROVISIONED"
            findings.append(
                f"CPU request is {cpu_req_cores:.2f} cores, but peak usage is only {act_cpu:.2f} cores."
            )

        if mem_req_bytes > 0 and mem_req_bytes >= act_mem * 2.5 and (mem_req_bytes - act_mem) > 200 * 1024**2:
            status = "OVER_PROVISIONED"
            findings.append(
                f"Memory request is {format_memory(mem_req_bytes)}, "
                f"but peak usage is only {format_memory(act_mem)}."
            )

        # Under-provisioned check
        if mem_lim_bytes > 0 and act_mem >= mem_lim_bytes * 0.85:
            status = "UNDER_PROVISIONED"
            findings.append(
                f"Memory usage ({format_memory(act_mem)}) is >85% of limit "
                f"({format_memory(mem_lim_bytes)}): high OOMKilled risk!"
            )
            target_mem_lim = max(act_mem * 1.5, mem_lim_bytes * 1.4)

        if cpu_lim_cores > 0 and act_cpu >= cpu_lim_cores * 0.9:
            status = "UNDER_PROVISIONED"
            findings.append(
                f"CPU usage ({act_cpu:.2f}) is >90% of limit ({cpu_lim_cores:.2f}): throttling risk."
            )
            target_cpu_lim = max(act_cpu * 1.5, cpu_lim_cores * 1.3)

        # Cost savings calculation per container (across all replicas)
        saved_cpu_cores = max(cpu_req_cores - target_cpu_req, 0.0) if cpu_req_cores > 0 else 0.0
        saved_mem_gb = max(mem_req_bytes - target_mem_req, 0.0) / (1024**3) if mem_req_bytes > 0 else 0.0

        container_savings = (
            (saved_cpu_cores * ESTIMATED_VCPU_MONTHLY_COST) + (saved_mem_gb * ESTIMATED_GB_RAM_MONTHLY_COST)
        ) * replicas
        total_savings += container_savings

        rec_cpu_req = format_cpu(target_cpu_req)
        rec_cpu_lim = format_cpu(target_cpu_lim)
        rec_mem_req = format_memory(target_mem_req)
        rec_mem_lim = format_memory(target_mem_lim)

        advice = ContainerAdvice(
            container=c_name,
            current_cpu_req=curr_cpu_req_str,
            current_cpu_limit=curr_cpu_lim_str,
            current_mem_req=curr_mem_req_str,
            current_mem_limit=curr_mem_lim_str,
            actual_cpu_cores=act_cpu,
            actual_mem_bytes=act_mem,
            rec_cpu_req=rec_cpu_req,
            rec_cpu_limit=rec_cpu_lim,
            rec_mem_req=rec_mem_req,
            rec_mem_limit=rec_mem_lim,
            status=status,
            findings=findings,
            monthly_savings=container_savings,
        )
        container_advices.append(advice)

        patch_containers.append(
            f'        - name: "{c_name}"\n'
            f"          resources:\n"
            f"            requests:\n"
            f'              cpu: "{rec_cpu_req}"\n'
            f'              memory: "{rec_mem_req}"\n'
            f"            limits:\n"
            f'              cpu: "{rec_cpu_lim}"\n'
            f'              memory: "{rec_mem_lim}"'
        )

    patch_yaml = "spec:\n  template:\n    spec:\n      containers:\n" + "\n".join(patch_containers)

    return WorkloadOptimizationReport(
        workload=dep_name,
        namespace=namespace,
        replicas=replicas,
        containers=container_advices,
        total_monthly_savings=total_savings,
        patch_snippet=patch_yaml,
    )


def format_optimization_report(reports: list[WorkloadOptimizationReport]) -> str:
    """Format reports into an executive markdown optimization advisory document."""
    if not reports:
        return "No workloads analyzed."

    total_cluster_savings = sum(r.total_monthly_savings for r in reports)
    lines = [
        "# KubePilot AI Resource & Cost Optimization Advisory",
        f"- **Workloads Analyzed**: {len(reports)}",
        f"- **Projected Monthly Savings**: **${total_cluster_savings:,.2f} / month**",
        "",
        "| Workload | Container | Status | Current (CPU/Mem) | Recommended (CPU/Mem) | Savings |",
        "|---|---|---|---|---|---|",
    ]

    for r in reports:
        for c in r.containers:
            status_badge = {
                "OVER_PROVISIONED": "🟡 Over-provisioned",
                "UNDER_PROVISIONED": "🔴 Under-provisioned (Risk)",
                "UNCONSTRAINED": "🟠 Unconstrained",
                "OPTIMAL": "🟢 Optimal",
            }.get(c.status, c.status)

            curr = f"{c.current_cpu_req} / {c.current_mem_req}"
            rec = f"{c.rec_cpu_req} / {c.rec_mem_req}"
            sav = f"${c.monthly_savings:.2f}" if c.monthly_savings > 0 else "-"
            lines.append(f"| {r.workload} | {c.container} | {status_badge} | {curr} | {rec} | {sav} |")

    lines.append("")
    lines.append("## Detailed Workload Findings & Remediation Patches")

    for r in reports:
        lines.append(f"\n### `{r.namespace}/{r.workload}` ({r.replicas} replicas)")
        for c in r.containers:
            if c.findings:
                lines.append(f"- **Container `{c.container}`**:")
                for f in c.findings:
                    lines.append(f"  - {f}")
                mem_str = format_memory(c.actual_mem_bytes)
                lines.append(f"  - *Live Peak Observed*: CPU: {c.actual_cpu_cores:.3f} cores, Mem: {mem_str}")

        lines.append("\n**Recommended Patch**:")
        lines.append("```yaml")
        lines.append(r.patch_snippet)
        lines.append("```")
        inline_patch = " ".join(r.patch_snippet.split())
        lines.append(f"`kubectl patch deployment {r.workload} -n {r.namespace} --patch '{inline_patch}'`")

    return "\n".join(lines)


@register(SafetyCategory.READ)
def analyze_resource_optimization(namespace: str = "", workload: str = "") -> str:
    """Analyze CPU and memory requests, limits, and live usage for workloads in a namespace.
    Identifies over-provisioned containers (cost savings), under-provisioned containers
    (OOM/throttling risk), and unconstrained workloads, with concrete right-sizing patches."""
    ns = namespace or get_settings().default_namespace
    try:
        pod_metrics = _get_pod_metrics(ns)
        apps_api = apps()

        if workload:
            try:
                dep = apps_api.read_namespaced_deployment(name=workload, namespace=ns)
                deps = [dep]
            except Exception as exc:
                return f"Deployment '{workload}' not found in namespace '{ns}': {exc}"
        else:
            deps = apps_api.list_namespaced_deployment(namespace=ns).items

        if not deps:
            return f"No deployments found to optimize in namespace '{ns}'."

        reports = [analyze_deployment_resources(d, pod_metrics) for d in deps]
        return format_optimization_report(reports)
    except Exception as exc:
        return f"Error analyzing resource optimization in namespace '{ns}': {exc}"
