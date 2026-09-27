from app.tools.registry import SafetyCategory, all_tools, get_tool


def test_expected_tools_registered():
    expected = {
        # Kubernetes
        "get_pods": SafetyCategory.READ,
        "get_deployments": SafetyCategory.READ,
        "get_services": SafetyCategory.READ,
        "get_events": SafetyCategory.READ,
        "get_logs": SafetyCategory.READ,
        "describe_resource": SafetyCategory.READ,
        "rollout_status": SafetyCategory.READ,
        "get_resource_usage": SafetyCategory.READ,
        "restart_deployment": SafetyCategory.OPERATIONAL,
        "scale_deployment": SafetyCategory.OPERATIONAL,
        "rollback_deployment": SafetyCategory.OPERATIONAL,
        # Prometheus / Loki / GitHub
        "prom_query": SafetyCategory.READ,
        "prom_query_range": SafetyCategory.READ,
        "loki_query_logs": SafetyCategory.READ,
        "get_repository": SafetyCategory.READ,
        "get_workflow": SafetyCategory.READ,
        "get_workflow_jobs": SafetyCategory.READ,
        "get_workflow_logs": SafetyCategory.READ,
        "get_commit": SafetyCategory.READ,
    }
    registered = {spec.name: spec.category for spec in all_tools()}
    for name, category in expected.items():
        assert registered.get(name) == category, f"tool {name} missing or miscategorized"


def test_every_tool_has_a_description():
    for spec in all_tools():
        assert spec.description, f"tool {spec.name} has no docstring/description"


def test_no_destructive_tools_exposed():
    assert all(spec.category is not SafetyCategory.DESTRUCTIVE for spec in all_tools())


def test_get_tool_lookup():
    assert get_tool("get_pods") is not None
    assert get_tool("does_not_exist") is None
