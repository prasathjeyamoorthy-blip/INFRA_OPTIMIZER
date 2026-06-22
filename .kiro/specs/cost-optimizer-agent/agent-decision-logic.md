# Agent Decision Logic — Dynamic Infrastructure Cost Optimizer

## Overview

The agent runs a continuous **observe → decide → act** loop. Every cycle, it reads live EC2
state and CloudWatch metrics, hands a compact summary to the LLM (Llama 3.3 70B via Groq),
and executes whatever actions the model returns — up to two real AWS actions per cycle.

The LLM is the **sole decision-maker**. No `if/else` logic anywhere in the codebase decides
which action to apply to an instance; that responsibility belongs entirely to the model.

---

## Full Cycle Sequence (10 Steps)

```
┌─────────────────────────────────────────────────────────┐
│  ORCHESTRATOR CYCLE  (orchestrator.py)                  │
│                                                         │
│  Step 1  ── Read kill switch state from DB              │
│  Step 2  ── observe_all()  → EC2 + CloudWatch state     │
│  Step 3  ── upsert_instance_snapshot() for each inst.   │
│  Step 4  ── get_recent_cycles(limit=50) from DB         │
│  Step 4b ── get_pending_resizes() → inject into state   │
│  Step 5  ── filter_eligible_instances()  [safety.py]    │
│                                                         │
│  ┌── if kill_switch OFF and eligible_ids not empty ──┐  │
│  │  Step 6  ── call_llm()           [llm.py]         │  │
│  │  Step 7  ── validate_actions()   [validator.py]   │  │
│  │  Step 8  ── TOOL_DISPATCH[tool](**action)         │  │
│  │             + post-action side effects            │  │
│  └────────────────────────────────────────────────────┘  │
│                                                         │
│  Step 9  ── write_cycle_records() to DB                 │
│  Step 10 ── sleep(POLL_INTERVAL)   [always runs]        │
└─────────────────────────────────────────────────────────┘
```

---

## Step 5 — Pre-LLM Safety Filter (`safety.py`)

Before the LLM sees any instance, two exclusion rules are applied:

| Rule | Logic | Effect |
|---|---|---|
| Protected tag | `tags["protected"] == "true"` | Permanently excluded every cycle |
| Cooldown | Instance received a real action in the last **3** cycle records | Excluded for 3 cycles after any real action |

Real actions that trigger cooldown: `stop_instance`, `start_instance`, `resize_instance`, `tag_instance`.

`do_nothing` and `alert_human` do **not** trigger cooldown.

The `cache-1` instance is excluded by the protected-tag rule. Its name is never
referenced anywhere in code — only the tag value is checked.

---

## Step 6 — What the LLM Receives

`llm.py` builds a compact text prompt instead of sending raw JSON arrays. This keeps
input tokens in the 200–400 range rather than 800–2000.

### Prompt structure

```
SYSTEM:
  You are an AWS cost-optimization agent.
  Respond with ONLY a valid JSON array. No markdown, no explanation.
  Each element: {"tool": "<action>", "instance_id": "<id>",
                 "reasoning": "<brief>", "new_type": "<optional>"}
  Tools: stop_instance | start_instance | resize_instance |
         tag_instance | do_nothing | alert_human
  Max 2 real actions per response.
  Every eligible instance must have exactly one action.

  DECISION RULES:
    - CPU >= 80%  → stop_instance  (will be resized after stopping)
    - CPU < 10% sustained → stop_instance  (save cost)
    - STOPPED + pending_resize set → resize_instance (new_type = pending value)
    - STOPPED + no pending_resize → start_instance if clear demand, else do_nothing
    - STOPPED + just resized → start_instance
    - Uncertain → alert_human


USER (per eligible instance):
  - {id} ({name}) type={instance_type} status={running|stopped}
    cpu={avg}%  disk_r={avg}MB  disk_w={avg}MB
    net_in={avg}MB  net_out={avg}MB
    [PENDING_RESIZE={target_type}]   ← only shown when set

  RECENT HISTORY (last 5):
    cycle N: <action> on <instance_name>
    ...
```

All metric values are **averages**, not raw CloudWatch arrays. History is capped
at the last 5 records to limit tokens further.

---

## Step 7 — Post-LLM Validation (`validator.py`)

Three rules are applied to every action the LLM returns, in order:

| Rule | Check | Outcome |
|---|---|---|
| 1. Tool whitelist | `tool` must be one of the 6 allowed tools | Drop if unknown |
| 2. Eligibility check | `instance_id` must be in the pre-filtered eligible list | Drop if not eligible |
| 3. Real-action cap | At most **2** real actions per cycle (stop/start/resize/tag) | Drop extras; `do_nothing` and `alert_human` are uncapped |

Invalid actions are silently dropped — no exception is raised.

---

## Step 8 — Execution & Side Effects (`orchestrator.py` + `executor.py`)

Each validated action is dispatched via `TOOL_DISPATCH[tool](**action)`.

### Tool dispatch table

| Tool | boto3 call | AWS effect |
|---|---|---|
| `stop_instance` | `ec2.stop_instances()` | Stops a running instance |
| `start_instance` | `ec2.start_instances()` | Starts a stopped instance |
| `resize_instance` | `ec2.modify_instance_attribute(Attribute="instanceType")` | Changes instance type (must be stopped) |
| `tag_instance` | `ec2.create_tags()` | Applies/updates EC2 tags |
| `do_nothing` | _(no call)_ | Returns `{"status": "no_op"}` |
| `alert_human` | _(no call)_ | Logs a WARNING; returns `{"status": "alerted"}` |

### Post-action side effects (orchestrator only)

After `stop_instance` executes, the orchestrator checks whether the stop was
triggered by high CPU (avg ≥ 80 %). If so, it calls `set_pending_resize()` and
records the next target type based on a fixed upgrade ladder:

```
t3.nano → t3.micro → t3.small → t3.medium → t3.large → t3.xlarge → t3.2xlarge
```

After `resize_instance` executes, `set_pending_resize(id, None)` clears the flag.

---

## Decision Scenarios

### Scenario 1 — High CPU Spike (avg ≥ 80 %)

```
Instance state : running
CPU avg        : ≥ 80 %

Cycle N
  LLM decision : stop_instance
  Execution    : EC2 stops the instance
  Side effect  : pending_resize = next_type written to DB
  Cooldown     : instance excluded from LLM for 3 cycles

Cycle N+3 (cooldown expired, instance is STOPPED, pending_resize visible)
  LLM sees     : status=stopped  PENDING_RESIZE=t3.small
  LLM decision : resize_instance  new_type=t3.small
  Execution    : EC2 modifies instance type
  Side effect  : pending_resize cleared

Cycle N+4 (instance is STOPPED, no pending_resize, just resized)
  LLM decision : start_instance
  Execution    : EC2 starts the instance
```

**Sequence diagram:**

```
Orchestrator          LLM              EC2 (boto3)          DB
     │                 │                    │                 │
     │── observe ──────►│                    │                 │
     │◄── state ────────│                    │                 │
     │── call_llm ──────►│                   │                 │
     │◄── stop_instance ─│                   │                 │
     │── validate ──────►│(pass)             │                 │
     │── stop_instances──────────────────────►                 │
     │◄── ok ────────────────────────────────│                 │
     │── set_pending_resize ────────────────────────────────── ►│
     │── write_cycle_records ───────────────────────────────── ►│
     │   [3 cycles pass — instance on cooldown]                │
     │── observe ──────►│                    │                 │
     │── call_llm ──────►│                   │                 │
     │◄── resize_instance(t3.small) ─────────│                 │
     │── modify_instance_attribute ──────────►                 │
     │── clear_pending_resize ──────────────────────────────── ►│
     │── call_llm (next cycle) ─────────────►│                 │
     │◄── start_instance ────────────────────│                 │
     │── start_instances─────────────────────►                 │
```

---

### Scenario 2 — Sustained Low CPU (avg < 10 %)

```
Instance state : running
CPU avg        : < 10 %  (idle — wasting cost)

Cycle N
  LLM decision : stop_instance
  Execution    : EC2 stops the instance
  Side effect  : avg_cpu < 80 %, so NO pending_resize is written
  Cooldown     : instance excluded for 3 cycles

Cycle N+3 (cooldown expired, instance is STOPPED, no pending_resize)
  LLM sees     : status=stopped, no demand signals in history
  LLM decision : do_nothing  (no clear demand → stay stopped to save cost)
```

---

### Scenario 3 — Stopped Instance With No Pending Resize and Clear Demand

```
Instance state : stopped
Pending resize : not set
Recent history : evidence of traffic / demand (high network activity, etc.)

Cycle N
  LLM decision : start_instance
  Execution    : EC2 starts the instance
```

---

### Scenario 4 — Ambiguous / Unknown Situation

```
Instance state : any
Metrics        : mixed signals — not clearly idle, not clearly overloaded

Cycle N
  LLM decision : alert_human
  Execution    : logs WARNING with reasoning; no AWS call made
  Cooldown     : NOT triggered (alert_human is not a real action)
```

---

### Scenario 5 — Protected Instance (cache-1)

```
Instance       : tags["protected"] == "true"
Pre-LLM filter : excluded by safety.py — never passed to LLM
LLM decision   : never sees this instance
AWS call       : none — zero risk of accidental action
```

---

### Scenario 6 — Kill Switch Active

```
Kill switch    : active (set via API or DB)

Every cycle:
  Step 1 : kill_active = True
  Steps 2–5 : observe + filter run as normal (DB stays current)
  Steps 6–8 : SKIPPED  — no LLM call, no AWS calls
  Step 9  : no records written (records list is empty)
  Step 10 : sleep as normal
```

The kill switch takes effect within one poll interval (default 10 s) without
restarting any process.

---

### Scenario 7 — Rate Limit on Groq API

```
Cycle N
  call_llm() → GroqRateLimitError on key 1
  llm.py rotates to key 2 → retries
  (repeats for each loaded key)
  If all keys exhausted → raises ValueError
  orchestrator.py catches Exception → logs traceback, continues to next cycle
```

Up to 5 API keys (`GROQ_API_KEY1`–`GROQ_API_KEY5`) are round-robin rotated on 429
errors. Key rotation is transparent to the orchestrator.

---

## Constraints Summary

| Constraint | Value | Enforced in |
|---|---|---|
| Max real actions per cycle | 2 | `validator.py` |
| Cooldown after real action | 3 cycles | `safety.py` |
| History sent to LLM | last 5 records | `llm.py` |
| History loaded from DB | last 50 records | `orchestrator.py` |
| Max LLM output tokens | 512 | `llm.py` |
| LLM temperature | 0.1 (near-deterministic) | `llm.py` |
| Model | llama-3.3-70b-versatile (Groq) | `llm.py` |
| CPU high threshold | 80 % | LLM system prompt |
| CPU low threshold | 10 % | LLM system prompt |
| Resize trigger | avg_cpu ≥ 80 % at stop time | `orchestrator.py` |

---

## Data Flow Summary

```
CloudWatch / EC2
      │
      ▼
 observer.py  ──► observe_all() returns state dict
      │
      ▼
 safety.py  ──► filter_eligible_instances() → eligible_ids[]
      │                 (removes protected + cooldown instances)
      ▼
 llm.py  ──► compact text prompt → Groq API → JSON array of actions
      │
      ▼
 validator.py  ──► validate_actions() → filtered + capped actions[]
      │                 (whitelist check + eligibility check + cap)
      ▼
 executor.py  ──► TOOL_DISPATCH[tool](**action) → boto3 call → AWS
      │
      ▼
 db.py  ──► write_cycle_records() → audit.db (SQLite)
      │
      ▼
 api.py  ──► REST endpoints → dashboard polls every 3 s
```
