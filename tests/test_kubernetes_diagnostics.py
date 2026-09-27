"""Tests for new Kubernetes diagnostics tools (previous logs, ingress, PVC, HPA, and cluster triage)."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app.tools.kubernetes import tools as k8s


def test_get_logs_previous_container():
    with patch.object(k8s, "core") as mock_core:
        mock_api = MagicMock()
        mock_api.read_namespaced_pod_log.return_value = "panic: runtime error\nexit code 2"
        mock_core.return_value = mock_api

        output = k8s.get_logs("crashed-pod", namespace="prod", previous=True)
        assert "previous terminated instance" in output
        assert "panic: runtime error" in output
        mock_api.read_namespaced_pod_log.assert_called_once_with(
            name="crashed-pod",
            namespace="prod",
            container=None,
            tail_lines=100,
            previous=True,
            timestamps=True,
        )


def test_get_ingresses():
    with patch.object(k8s, "networking") as mock_net:
        mock_api = MagicMock()
        mock_ingress = SimpleNamespace(
            metadata=SimpleNamespace(name="api-ingress", creation_timestamp=None),
            spec=SimpleNamespace(
                ingress_class_name="nginx",
                tls=[SimpleNamespace(hosts=["api.example.com"])],
                rules=[
                    SimpleNamespace(
                        host="api.example.com",
                        http=SimpleNamespace(
                            paths=[
                                SimpleNamespace(
                                    path="/v1",
                                    backend=SimpleNamespace(
                                        service=SimpleNamespace(
                                            name="api-svc",
                                            port=SimpleNamespace(number=8080, name=None),
                                        )
                                    ),
                                )
                            ]
                        ),
                    )
                ],
            ),
        )
        mock_api.list_namespaced_ingress.return_value = SimpleNamespace(items=[mock_ingress])
        mock_net.return_value = mock_api

        res = k8s.get_ingresses(namespace="default")
        assert "api-ingress" in res
        assert "api.example.com/v1 -> api-svc:8080" in res
        assert "tls=[api.example.com]" in res


def test_get_pvc_status():
    with patch.object(k8s, "core") as mock_core:
        mock_api = MagicMock()
        mock_pvc = SimpleNamespace(
            metadata=SimpleNamespace(name="data-pvc", creation_timestamp=None),
            spec=SimpleNamespace(
                storage_class_name="standard",
                volume_name="pvc-12345",
                access_modes=["ReadWriteOnce"],
                resources=SimpleNamespace(requests={"storage": "10Gi"}),
            ),
            status=SimpleNamespace(phase="Bound", capacity={"storage": "10Gi"}),
        )
        mock_api.list_namespaced_persistent_volume_claim.return_value = SimpleNamespace(items=[mock_pvc])
        mock_core.return_value = mock_api

        res = k8s.get_pvc_status(namespace="default")
        assert "data-pvc" in res
        assert "status=Bound" in res
        assert "capacity=10Gi" in res


def test_get_hpa():
    with patch.object(k8s, "autoscaling") as mock_auto:
        mock_api = MagicMock()
        mock_hpa = SimpleNamespace(
            metadata=SimpleNamespace(name="web-hpa", creation_timestamp=None),
            spec=SimpleNamespace(
                scale_target_ref=SimpleNamespace(kind="Deployment", name="web"),
                min_replicas=2,
                max_replicas=10,
            ),
            status=SimpleNamespace(
                current_replicas=4,
                desired_replicas=4,
                conditions=[SimpleNamespace(status="True", type="AbleToScale", reason="Ready", message="")],
            ),
        )
        mock_api.list_namespaced_horizontal_pod_autoscaler.return_value = SimpleNamespace(items=[mock_hpa])
        mock_auto.return_value = mock_api

        res = k8s.get_hpa(namespace="default")
        assert "web-hpa" in res
        assert "target=Deployment/web" in res
        assert "min=2 max=10 current=4 desired=4" in res


def test_describe_resource_expanded():
    with (
        patch.object(k8s, "networking") as mock_net,
        patch.object(k8s, "core") as mock_core,
        patch.object(k8s, "autoscaling") as mock_auto,
        patch.object(k8s, "get_events", return_value=""),
    ):
        mock_net.return_value.read_namespaced_ingress.return_value = SimpleNamespace(
            metadata={"name": "ing-1"}
        )
        mock_core.return_value.read_namespaced_persistent_volume_claim.return_value = SimpleNamespace(
            metadata={"name": "pvc-1"}
        )
        mock_auto.return_value.read_namespaced_horizontal_pod_autoscaler.return_value = SimpleNamespace(
            metadata={"name": "hpa-1"}
        )

        assert "ing-1" in k8s.describe_resource("ingress", "ing-1", "default")
        assert "pvc-1" in k8s.describe_resource("pvc", "pvc-1", "default")
        assert "hpa-1" in k8s.describe_resource("hpa", "hpa-1", "default")


def test_cluster_health_scan_detects_issues():
    with (
        patch.object(k8s, "core") as mock_core,
        patch.object(k8s, "apps") as mock_apps,
    ):
        core_api = MagicMock()
        apps_api = MagicMock()

        # Unhealthy node
        unhealthy_node = SimpleNamespace(
            metadata=SimpleNamespace(name="node-bad"),
            status=SimpleNamespace(
                conditions=[
                    SimpleNamespace(type="Ready", status="False"),
                    SimpleNamespace(type="MemoryPressure", status="True"),
                ]
            ),
        )
        core_api.list_node.return_value = SimpleNamespace(items=[unhealthy_node])

        # Crashing pod
        bad_pod = SimpleNamespace(
            metadata=SimpleNamespace(name="crash-pod", namespace="default"),
            status=SimpleNamespace(
                phase="Running",
                container_statuses=[
                    SimpleNamespace(
                        name="c1",
                        restart_count=12,
                        state=SimpleNamespace(
                            waiting=SimpleNamespace(reason="CrashLoopBackOff"),
                            running=None,
                            terminated=None,
                        ),
                        last_state=SimpleNamespace(
                            waiting=None,
                            running=None,
                            terminated=SimpleNamespace(reason="OOMKilled"),
                        ),
                    )
                ],
            ),
        )
        core_api.list_pod_for_all_namespaces.return_value = SimpleNamespace(items=[bad_pod])

        # Stuck deployment
        stuck_dep = SimpleNamespace(
            metadata=SimpleNamespace(name="web-dep", namespace="prod"),
            spec=SimpleNamespace(replicas=3),
            status=SimpleNamespace(ready_replicas=1, unavailable_replicas=2),
        )
        apps_api.list_deployment_for_all_namespaces.return_value = SimpleNamespace(items=[stuck_dep])

        # Warning event
        warn_event = SimpleNamespace(
            last_timestamp=None,
            event_time=None,
            first_timestamp=None,
            involved_object=SimpleNamespace(namespace="default", kind="Pod", name="crash-pod"),
            reason="BackOff",
            message="Back-off restarting failed container",
        )
        core_api.list_event_for_all_namespaces.return_value = SimpleNamespace(items=[warn_event])

        mock_core.return_value = core_api
        mock_apps.return_value = apps_api

        report = k8s.cluster_health_scan()
        assert "KubePilot Cluster Health Triage" in report
        assert "Unhealthy Nodes" in report
        assert "node-bad" in report
        assert "Problematic Pods" in report
        assert "crash-pod" in report
        assert "Degraded Deployments" in report
        assert "web-dep" in report
        assert "Recent Warning Events" in report
        assert "potential issue(s) detected" in report
