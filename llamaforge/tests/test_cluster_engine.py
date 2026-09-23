import pytest

from llamaforge.core.cluster import ClusterNode, ClusterScheduler, NodeBenchmark, NodeLimits
from llamaforge.core.processes import ProcessPolicy


def node(name, ram, cpu=8, net=1000, ping=1.0, score=1000, enabled=True):
    return ClusterNode(
        node_id=name, hostname=name, ip="192.168.1.10", online=True, paired=True,
        hardware={"ram_total_gb": ram + 2, "ram_available_gb": ram + 0.5, "logical_cores": cpu, "gpus": [], "link_speed_mbps": net},
        limits=NodeLimits(enabled=enabled, ram_mode="manual", ram_limit_gb=ram, cpu_mode="manual", cpu_threads=cpu),
        benchmark=NodeBenchmark(cpu_score=score, memory_bandwidth_gbps=30, network_mbps=net, latency_ms=ping, measured_at=1),
        network_mbps=net, ping_ms=ping,
    )


def test_scheduler_enforces_manual_ram_hard_budget_in_plan():
    p = ClusterScheduler().plan([node("a", 5), node("b", 6)], model_size_gb=8, runtime_overhead_gb=1, optimization_mode="smart", selection_mode="force_selected")
    assert p.required_ram_gb == 9
    for row in p.nodes:
        assert row["ram_allocation_gb"] <= row["ram_limit_gb"]
    assert sum(row["ram_allocation_gb"] for row in p.nodes) >= p.required_ram_gb - 0.02


def test_ram_allocation_is_independent_from_compute_share():
    slow_big = node("slow-big", 8, cpu=4, score=250)
    fast_small = node("fast-small", 4, cpu=16, score=4000)
    p = ClusterScheduler().plan([slow_big, fast_small], 8, 1, "maximum_compute", "force_selected")
    rows = {x["node_id"]: x for x in p.nodes}
    assert rows["fast-small"]["compute_share"] > rows["slow-big"]["compute_share"]
    assert rows["slow-big"]["ram_limit_gb"] > rows["fast-small"]["ram_limit_gb"]


def test_auto_scheduler_may_skip_unhelpful_workers():
    nodes = [node("fast", 8, score=5000, net=2500, ping=.3), node("okay", 5, score=1200), node("slow", 5, score=120, net=100, ping=20), node("very-slow", 5, score=50, net=50, ping=30)]
    p = ClusterScheduler().plan(nodes, 7, 0.8, "smart", "auto")
    assert 1 <= len(p.nodes) < len(nodes)
    assert "fast" in {x["node_id"] for x in p.nodes}


def test_force_selected_uses_all_enabled_nodes_when_viable():
    nodes = [node("a", 4), node("b", 4), node("c", 4)]
    p = ClusterScheduler().plan(nodes, 7, 1, "smart", "force_selected")
    assert {x["node_id"] for x in p.nodes} == {"a", "b", "c"}


def test_disabled_worker_is_never_scheduled():
    p = ClusterScheduler().plan([node("enabled", 7), node("disabled", 10, enabled=False)], 5, .7, "smart", "auto")
    assert {x["node_id"] for x in p.nodes} == {"enabled"}


def test_insufficient_allowed_ram_fails_before_model_launch():
    with pytest.raises(RuntimeError, match="Cluster RAM is insufficient"):
        ClusterScheduler().plan([node("a", 2), node("b", 2)], 8, 1, "maximum_model_size", "force_selected")


def test_process_policy_has_real_hard_memory_contract():
    p = ProcessPolicy(memory_limit_mb=2048, memory_limit_required=True)
    assert p.memory_limit_mb == 2048 and p.memory_limit_required is True
