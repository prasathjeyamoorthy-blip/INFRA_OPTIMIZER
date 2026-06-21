---
inclusion: fileMatch
fileMatchPattern: "publisher.py"
---

# Synthetic Metric Publisher

Standalone process (`python publisher.py`). Pushes fake-but-controlled metrics to CloudWatch so the demo scenario plays out reliably. Every real downstream action (EC2 start/stop) is still genuinely executed by the agent — only the triggering data is synthetic.

## Clock
One simulated hour = 10 real seconds. Push one data point every 10 seconds. Advance the `Timestamp` parameter by 1 hour on each push.

## CloudWatch target
- Namespace: `CostOptimizerAgent`
- Dimension: `InstanceId`
- Metrics: `CpuUtilizationPercent`, `LatencyMs`
- Unit: `"None"` for both

## Three-phase scenario

**Phase 1 — idle_web2** (3 cycles, then advance)
web-1: CPU 62%, Latency 85ms
web-2: CPU 3%, Latency 80ms
worker-1: CPU 71%, no latency
cache-1: CPU 12%, no latency

**Phase 2 — latency_spike** (2 cycles, then advance)
web-1: CPU 68%, Latency 510ms  ← 6x spike
web-2: CPU 0%, no latency  ← agent has stopped it by now
worker-1: CPU 70%, no latency
cache-1: CPU 13%, no latency

**Phase 3 — recovered** (hold indefinitely)
web-1: CPU 61%, Latency 88ms
web-2: CPU 4%, Latency 82ms
worker-1: CPU 69%, no latency
cache-1: CPU 11%, no latency

## Instance name → ID mapping
On startup, call `ec2.describe_instances()` filtered by tag `project=cost-optimizer-agent` to build a `name → instance_id` map. Use this map for the `InstanceId` dimension value. Do not hardcode instance IDs.

## Skip metrics with None value
If a metric value for an instance is `None` in the scenario table, do not push a data point for it that cycle.

## Phase advancement
Track cycle count per phase. When a phase's `duration_cycles` is reached, increment phase index. Phase 3 never advances — hold it until the process is killed.
