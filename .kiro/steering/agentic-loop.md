---
inclusion: fileMatch
fileMatchPattern: "orchestrator.py|observer.py|safety.py|llm.py|validator.py|executor.py"
---

# Agentic Loop

## Cycle sequence (orchestrator.py)
Every `POLL_INTERVAL` seconds, in this exact order:

1. Check kill switch — if active, log `do_nothing` for all instances with reason `"Kill switch active."` and return early
2. `observe_all()` → full state dict
3. `filter_eligible_instances(state, recent_history)` → list of eligible instance IDs
4. `get_recent_cycles(limit=10)` → history for LLM context
5. `call_llm(state, eligible_ids, history)` → proposed actions
6. `validate_actions(proposed, state, eligible_ids)` → approved actions
7. `execute_actions(approved)` → results with aws_response per action
8. `upsert_instance_snapshot(state, approved, results)` → update `instances` table
9. `write_cycle_records(cycle_num, approved, results)` → append to `cycles` table
10. Sleep

Wrap the entire cycle body in try/except. Log the error. Never break the loop.

## observer.py
- `ec2.describe_instances()` filtered by tag `project=cost-optimizer-agent`
- `cloudwatch.get_metric_data()` for last 3 points per instance, namespace `CostOptimizerAgent`, metrics: `CpuUtilizationPercent` and `LatencyMs`, dimension: `InstanceId`
- Return: `{ instance_id: { name, ec2_state, instance_type, tags, cpu_history, latency_history } }`

## safety.py
`filter_eligible_instances(state, recent_history) -> list[str]`
- Exclude instances where tag `protected == "true"` — always, permanently
- Exclude instances that had a real action (anything except `do_nothing` or `alert_human`) in the last 3 cycles
- Return list of eligible instance IDs

Non-eligible instances still appear in the LLM context and must appear in the response — with `do_nothing`.

## llm.py
One `claude-sonnet-4-6` call per cycle. Max tokens: 1024.

**System prompt must enforce:**
- Raw numbers only — never label metrics as "idle", "high", or "anomalous"
- `do_nothing` is a deliberate logged outcome, not a fallback
- Response is a JSON array only — no markdown, no preamble
- Every object must include: `instance_id`, `instance_name`, `action`, `params`, `reasoning`
- Reasoning must reference history when relevant — especially when reversing a prior action

**User message must include:**
- Raw metric history (last 3 points) per instance
- Current EC2 state and tags per instance
- Eligibility status per instance (non-eligible must be noted so LLM knows to use `do_nothing`)
- Last 10 cycle records: cycle #, instance, action, aws_response, reasoning
- Full tool list with one-line descriptions

**Response parsing:** strip markdown fences before `json.loads()`. Raise `ValueError` on parse failure — the orchestrator's try/except will catch it and continue to the next cycle.

## validator.py
`validate_actions(llm_response, state, eligible_ids) -> list[dict]`

Rules applied in order:
1. Unknown tool name → override to `do_nothing`, set `validated=False`, append note to reasoning
2. Real action on non-eligible instance → same override
3. More than 2 real actions per cycle (not counting `do_nothing`/`alert_human`) → override excess to `do_nothing`
4. Everything else → `validated=True`, pass through

## executor.py
Six tools. The LLM returns a tool name; the dispatcher calls the matching function.

| Tool | boto3 call | Returns |
|---|---|---|
| `stop_instance` | `ec2.stop_instances()` | `"success"` or `"error: ..."` |
| `start_instance` | `ec2.start_instances()` | `"success"` or `"error: ..."` |
| `resize_instance` | stop → modify → start (blocking) | `"success"` or `"error: ..."` |
| `tag_instance` | `ec2.create_tags()` | `"success"` or `"error: ..."` |
| `do_nothing` | no call | `"skipped"` |
| `alert_human` | no call, log warning | `"flagged"` |

Dispatcher pattern: `TOOL_DISPATCH[action["action"]](action["instance_id"], **action.get("params", {}))`

## AWS namespace
CloudWatch namespace: `CostOptimizerAgent`
Dimension name: `InstanceId`
Metric names: `CpuUtilizationPercent`, `LatencyMs`
