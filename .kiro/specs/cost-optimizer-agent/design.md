# Design Document — Cost Optimizer Agent

## Overview

The Cost Optimizer Agent is an autonomous observe–decide–act system composed of four concurrently running processes: the Orchestrator loop, the synthetic metric Publisher, the FastAPI/uvicorn API Server, and the SQLite database layer. All Python source files live in `Agent/`. The LLM (Claude claude-sonnet-4-6) is the sole decision-maker; no `if/else` logic in any module decides which action to apply to an instance.

---

## Architecture

### High-Level Process Map

```
┌─────────────────────────────────────────────────────────────┐
│  Terminal / Dashboard  (external consumer, polls every 3s)  │
└──────────────────────────┬──────────────────────────────────┘
                           │ HTTP  (REST)
             ┌─────────────▼─────────────┐
             │  API Server (main.py)     │  FastAPI + uvicorn
             │  api.py route handlers    │  port from .env
             └─────────────┬─────────────┘
                           │ SQLAlchemy reads
                           ▼
                    ┌──────────────┐
                    │  audit.db    │  SQLite
                    └──────┬───────┘
                           │ SQLAlchemy writes
          ┌────────────────┼────────────────────┐
          │                │                    │
┌─────────▼──────┐  ┌──────▼───────┐  ┌────────▼───────┐
│  Orchestrator  │  │  Publisher   │  │  (future procs) │
│  orchestrator  │  │  publisher   │  └─────────────────┘
│  .py           │  │  .py         │
└────────┬───────┘  └──────┬───────┘
         │                 │
         │ boto3           │ boto3 (CloudWatch PutMetricData)
         ▼                 ▼
    ┌──────────────────────────┐
    │       AWS              │
    │  EC2 + CloudWatch      │
    └──────────────────────────┘
```

### Orchestrator Cycle (Single-Process, Sequential)

```
START cycle
  1. is_kill_switch_active()           → db.py
  2. observe_all()                     → observer.py  (EC2 + CloudWatch reads)
  3. upsert_instance_snapshot() × N   → db.py
  4. get_recent_cycles(limit=50)       → db.py
  5. filter_eligible_instances()       → safety.py
  ── if kill switch active: skip 6–8 ──
  6. call_llm()                        → llm.py       (1 Claude API call)
  7. validate_actions()                → validator.py
  8. TOOL_DISPATCH[action]() × M      → executor.py
  ─────────────────────────────────────
  9. write_cycle_records()             → db.py
  10. sleep(POLL_INTERVAL)
END cycle  →  repeat
```

---

## Module Design

### `models.py` — SQLAlchemy Table Definitions

Three ORM models:

```python
class Instance(Base):
    __tablename__ = "instances"
    id                  = Column(String, primary_key=True)   # EC2 instance-id
    name                = Column(String)
    instance_type       = Column(String)
    status              = Column(String)
    tags                = Column(String)                     # JSON string
    last_action         = Column(String, nullable=True)
    last_action_reasoning = Column(String, nullable=True)
    updated_at          = Column(DateTime, default=func.now(), onupdate=func.now())

class Cycle(Base):
    __tablename__ = "cycles"
    id              = Column(Integer, primary_key=True, autoincrement=True)
    cycle           = Column(Integer)
    timestamp       = Column(DateTime, default=func.now())
    instance_id     = Column(String)
    instance_name   = Column(String)
    action          = Column(String)
    reasoning       = Column(String)
    validated       = Column(Boolean)
    aws_response    = Column(String)    # JSON string

class KillSwitch(Base):
    __tablename__ = "kill_switch"
    id          = Column(Integer, primary_key=True, autoincrement=True)
    active      = Column(Boolean, nullable=False)
    toggled_at  = Column(DateTime, default=func.now())
```

### `db.py` — Session Factory and Helper Functions

```python
# Session factory (thread-safe for concurrent API + Orchestrator access)
engine = create_engine(
    os.environ["DB_URL"],
    connect_args={"check_same_thread": False}
)
SessionLocal = sessionmaker(bind=engine)

def init_db() -> None:
    """Create all tables if they do not exist (called at startup)."""
    Base.metadata.create_all(engine)

def upsert_instance_snapshot(instance_data: dict) -> None:
    """Insert or update a row in `instances` keyed on instance_data["id"]."""

def write_cycle_records(records: list[dict]) -> None:
    """Bulk-insert rows into `cycles`."""

def is_kill_switch_active() -> bool:
    """Return active field of the most recent kill_switch row; False if table empty."""

def get_recent_cycles(limit: int = 50) -> list[dict]:
    """Return the most recent `limit` cycle rows ordered by timestamp DESC."""
```

`DB_URL` is read from the environment; the default in `.env.example` is `sqlite:///./audit.db`. `check_same_thread=False` satisfies SQLite's requirement when multiple threads share one connection.

---

### `observer.py` — AWS State Reader

```python
def observe_all() -> dict:
    """
    Returns:
        {
            "instances": [
                {
                    "id": str,
                    "name": str,
                    "instance_type": str,
                    "status": str,
                    "tags": dict,
                    "metrics": {
                        "CpuUtilizationPercent": [float, ...],  # last 3 points
                        "LatencyMs":             [float, ...]
                    }
                },
                ...
            ]
        }
    """
```

Steps inside `observe_all()`:
1. Build a boto3 EC2 client using credentials from environment.
2. Call `ec2.describe_instances(Filters=[{"Name": "tag:project", "Values": ["cost-optimizer-agent"]}])`.
3. For each discovered instance, call `cloudwatch.get_metric_data()` requesting the last 3 data points of `CpuUtilizationPercent` and `LatencyMs` in namespace `CostOptimizerAgent`, dimensioned by instance name.
4. Return the assembled dict. Any boto3 exception propagates unhandled to the Orchestrator's `try/except`.

---

### `safety.py` — Pre-LLM Safety Filter

```python
REAL_ACTIONS = {"stop_instance", "start_instance", "resize_instance", "tag_instance"}
COOLDOWN_CYCLES = 3

def filter_eligible_instances(state: dict, recent_history: list) -> list[str]:
    """
    Returns list of instance IDs eligible for LLM consideration this cycle.

    Exclusion rules (applied in order, unconditionally):
      1. Any instance whose tags["protected"] == "true"  →  excluded permanently.
      2. Any instance that received a Real Action in the last COOLDOWN_CYCLES cycle
         records in recent_history  →  excluded this cycle.
    """
```

Key design decisions:
- Rule 1 reads `tags["protected"]` — **never** compares `instance["name"]` against any string. This is the sole mechanism protecting `cache-1` and satisfies Requirement 6.4.
- `recent_history` is the list of dicts from `get_recent_cycles()`. The function looks at `record["action"]` and `record["instance_id"]` and checks whether the action is a Real Action and whether the cycle number is within the last 3.
- The function is pure: no database or AWS calls; fully testable without mocks.

---

### `llm.py` — Claude Decision Module

```python
def call_llm(state: dict, eligible_ids: list[str], history: list[dict]) -> list[dict]:
    """
    Makes exactly one call to Claude claude-sonnet-4-6.
    Returns a list of action dicts, e.g.:
        [{"tool": "stop_instance", "instance_id": "i-abc", "reasoning": "..."}]
    Raises ValueError if the response cannot be parsed as a JSON array.
    """
```

Prompt structure sent to Claude:

```
You are an autonomous AWS cost-optimization agent.
Your job: analyze the current EC2 instance state and metrics, then decide
what infrastructure actions to take to minimize cost while maintaining
required capacity.

ELIGIBLE INSTANCE IDs (you may ONLY act on these):
{eligible_ids}

CURRENT STATE:
{json.dumps(state, indent=2)}

RECENT ACTION HISTORY (last 50 cycles):
{json.dumps(history, indent=2)}

Respond with ONLY a valid JSON array. No markdown, no explanation.
Each element must have: "tool", "instance_id", "reasoning".
Available tools: stop_instance, start_instance, resize_instance,
                 tag_instance, do_nothing, alert_human.
```

Response handling:
1. Strip leading/trailing whitespace and markdown code fences (` ```json ` ... ` ``` `).
2. Parse with `json.loads()`. If this raises, re-raise as `ValueError`.
3. Return the parsed list.

---

### `validator.py` — Post-LLM Safety Rails

```python
ALLOWED_TOOLS = {
    "stop_instance", "start_instance", "resize_instance",
    "tag_instance", "do_nothing", "alert_human"
}
REAL_ACTIONS = {"stop_instance", "start_instance", "resize_instance", "tag_instance"}
MAX_REAL_ACTIONS_PER_CYCLE = 2

def validate_actions(
    llm_response: list[dict],
    state: dict,
    eligible_ids: list[str]
) -> list[dict]:
    """
    Filters llm_response to only safe, valid actions.

    Rules applied in order:
      1. Drop any action whose "tool" is not in ALLOWED_TOOLS.
      2. Drop any action whose "instance_id" is not in eligible_ids.
      3. After rules 1-2, cap Real Actions at MAX_REAL_ACTIONS_PER_CYCLE (2);
         keep the first 2 real actions, drop the rest.
         Non-real actions (do_nothing, alert_human) are not counted against the cap.

    Returns filtered + capped list.
    """
```

No AWS calls. Pure function — fully unit-testable.

---

### `executor.py` — boto3 Tool Dispatch

```python
def stop_instance(instance_id: str, **kwargs) -> dict: ...
def start_instance(instance_id: str, **kwargs) -> dict: ...
def resize_instance(instance_id: str, new_type: str, **kwargs) -> dict: ...
def tag_instance(instance_id: str, tags: dict, **kwargs) -> dict: ...
def do_nothing(instance_id: str, **kwargs) -> dict: ...
def alert_human(instance_id: str, message: str, **kwargs) -> dict: ...

TOOL_DISPATCH: dict[str, Callable] = {
    "stop_instance":   stop_instance,
    "start_instance":  start_instance,
    "resize_instance": resize_instance,
    "tag_instance":    tag_instance,
    "do_nothing":      do_nothing,
    "alert_human":     alert_human,
}
```

boto3 mapping:

| Tool | boto3 call |
|---|---|
| `stop_instance` | `ec2.stop_instances(InstanceIds=[instance_id])` |
| `start_instance` | `ec2.start_instances(InstanceIds=[instance_id])` |
| `resize_instance` | `ec2.modify_instance_attribute(InstanceId=id, Attribute="instanceType", Value=new_type)` |
| `tag_instance` | `ec2.create_tags(Resources=[id], Tags=[...])` |
| `do_nothing` | No API call; returns `{"status": "no_op"}` |
| `alert_human` | `logging.warning(message)`; returns `{"status": "alerted", "message": message}` |

Each tool returns a dict that is stored as JSON in `cycles.aws_response`.

---

### `orchestrator.py` — Main Loop

```python
import time, logging
from db import init_db, is_kill_switch_active, upsert_instance_snapshot, \
               get_recent_cycles, write_cycle_records
from observer import observe_all
from safety import filter_eligible_instances
from llm import call_llm
from validator import validate_actions
from executor import TOOL_DISPATCH

def run():
    init_db()
    cycle_num = 0
    poll_interval = int(os.getenv("POLL_INTERVAL", "10"))

    while True:
        cycle_num += 1
        try:
            # Step 1 — kill switch check
            kill_active = is_kill_switch_active()

            # Step 2 — observe
            state = observe_all()

            # Step 3 — persist snapshots
            for inst in state["instances"]:
                upsert_instance_snapshot(inst)

            # Step 4 — recent history
            history = get_recent_cycles(limit=50)

            # Step 5 — filter
            eligible_ids = filter_eligible_instances(state, history)

            records = []
            if not kill_active:
                # Step 6 — LLM decides
                llm_actions = call_llm(state, eligible_ids, history)

                # Step 7 — validate
                validated = validate_actions(llm_actions, state, eligible_ids)

                # Step 8 — execute
                for action in validated:
                    tool_fn = TOOL_DISPATCH[action["tool"]]
                    aws_resp = tool_fn(**action)
                    records.append({
                        "cycle": cycle_num,
                        "instance_id":   action.get("instance_id"),
                        "instance_name": _name_for(state, action.get("instance_id")),
                        "action":        action["tool"],
                        "reasoning":     action.get("reasoning", ""),
                        "validated":     True,
                        "aws_response":  json.dumps(aws_resp),
                    })

            # Step 9 — persist
            if records:
                write_cycle_records(records)

        except Exception as exc:
            logging.exception("Cycle %d failed: %s", cycle_num, exc)

        # Step 10 — sleep
        time.sleep(poll_interval)
```

`_name_for()` is a small helper that looks up the instance name from the `state` dict by ID — zero hardcoded names.

---

### `api.py` + `main.py` — FastAPI REST Server

#### Routes

| Method | Path | Query params | Body | Returns |
|---|---|---|---|---|
| GET | `/api/instances` | — | — | `list[InstanceSchema]` |
| GET | `/api/cycles` | `limit: int = 50` | — | `list[CycleSchema]` |
| GET | `/api/cost` | — | — | `CostSummarySchema` |
| GET | `/api/killswitch` | — | — | `KillSwitchSchema` |
| POST | `/api/killswitch` | — | `{"active": bool}` | `KillSwitchSchema` |

All routes are registered with `CORSMiddleware(allow_origins=["*"])`.

#### Pydantic Schemas (`api.py`)

```python
class InstanceSchema(BaseModel):
    id: str
    name: str
    instance_type: str
    status: str
    tags: dict
    last_action: str | None
    last_action_reasoning: str | None
    updated_at: datetime

class CycleSchema(BaseModel):
    id: int
    cycle: int
    timestamp: datetime
    instance_id: str
    instance_name: str
    action: str
    reasoning: str
    validated: bool
    aws_response: str

class CostSummarySchema(BaseModel):
    total_instances: int
    running_count: int
    stopped_count: int
    estimated_hourly_usd: float  # derived from instance_type pricing table read from env/config

class KillSwitchSchema(BaseModel):
    active: bool
    toggled_at: datetime | None
```

#### `main.py`

```python
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import uvicorn
from api import router
from db import init_db

app = FastAPI(title="Cost Optimizer Agent API")
app.add_middleware(CORSMiddleware, allow_origins=["*"],
                   allow_methods=["*"], allow_headers=["*"])
app.include_router(router)

@app.on_event("startup")
def startup():
    init_db()

if __name__ == "__main__":
    uvicorn.run("main:app",
                host=os.getenv("API_HOST", "0.0.0.0"),
                port=int(os.getenv("API_PORT", "8000")),
                reload=False)
```

---

### `publisher.py` — Synthetic CloudWatch Metric Publisher

```python
NAMESPACE = "CostOptimizerAgent"
METRICS   = ["CpuUtilizationPercent", "LatencyMs"]
PUBLISH_INTERVAL = 10  # seconds, fixed regardless of POLL_INTERVAL

# Three phases of metric values (cycle-count driven)
PHASES = [
    # Phase 0 (cycles 0–9):   low utilization
    {"CpuUtilizationPercent": 15.0, "LatencyMs": 80.0},
    # Phase 1 (cycles 10–19): medium utilization
    {"CpuUtilizationPercent": 55.0, "LatencyMs": 200.0},
    # Phase 2 (cycles 20+):   high utilization
    {"CpuUtilizationPercent": 85.0, "LatencyMs": 450.0},
]

def _current_phase(cycle: int) -> dict:
    if cycle < 10:
        return PHASES[0]
    elif cycle < 20:
        return PHASES[1]
    return PHASES[2]

def run(instance_names: list[str]):
    cw = boto3.client("cloudwatch", ...)
    cycle = 0
    while True:
        phase = _current_phase(cycle)
        for name in instance_names:
            for metric_name, value in phase.items():
                cw.put_metric_data(
                    Namespace=NAMESPACE,
                    MetricData=[{
                        "MetricName": metric_name,
                        "Dimensions": [{"Name": "InstanceName", "Value": name}],
                        "Value": value,
                        "Unit": "None",
                    }]
                )
        cycle += 1
        time.sleep(PUBLISH_INTERVAL)
```

The publisher does not hardcode instance names — it receives them at runtime (e.g., by calling `observe_all()` once at startup or accepting them as arguments from `main.py`).

---

### `setup_aws.py` — One-Time Provisioning Script

Sequence of operations:
1. Launch 4 `t3.micro` instances with names `web-1`, `web-2`, `worker-1`, `cache-1`.
2. Apply `project=cost-optimizer-agent` to all four.
3. Apply `protected=true` to `cache-1`, `protected=false` to the other three.
4. Create IAM user `cost-optimizer-agent` with inline policy granting only the 7 specified actions.
5. Generate access key for the user.
6. Write `AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY` to `.env` in the project root (creating or updating the file without overwriting other keys).

---

### `test_smoke.py` — Smoke Tests (No Live AWS)

Tests run in order; any failure prints the assertion message and calls `sys.exit(1)`. On full pass, prints summary and exits with `0`.

| Test | Method |
|---|---|
| All module imports succeed | `import` each module inside a try/except |
| `filter_eligible_instances` excludes `protected=true` | Build mock state dict; assert ID not in result |
| `filter_eligible_instances` does not check names | Pass instance with `protected=true` tag but unusual name; assert excluded |
| `validate_actions` rejects unknown tool | Build action with `tool="hack_aws"`; assert dropped |
| `validate_actions` caps Real Actions at 2 | Build list of 5 stop actions; assert len(result) == 2 |
| `is_kill_switch_active` returns bool from in-memory DB | Create in-memory SQLite, insert a row, assert return type and value |

---

### `.env.example`

```
AWS_ACCESS_KEY_ID=your_key_here
AWS_SECRET_ACCESS_KEY=your_secret_here
AWS_DEFAULT_REGION=us-east-1
ANTHROPIC_API_KEY=your_anthropic_key_here
DB_URL=sqlite:///./audit.db
POLL_INTERVAL=10
API_HOST=0.0.0.0
API_PORT=8000
```

---

### `.gitignore`

```
.env
audit.db
__pycache__/
*.pyc
*.pyo
.pytest_cache/
```

---

### `requirements.txt` (pinned, in `Agent/`)

```
boto3==1.34.69
anthropic==0.25.1
fastapi==0.110.1
uvicorn==0.29.0
sqlalchemy==2.0.29
pydantic==2.6.4
python-dotenv==1.0.1
```

---

## Data Flow

### Observe → Filter → Decide → Execute → Persist

```
observe_all()
    └─ ec2.describe_instances()        →  raw EC2 state
    └─ cloudwatch.get_metric_data()    →  last 3 metric points per instance
    └─ returns: state dict

upsert_instance_snapshot() × N        →  instances table updated

get_recent_cycles(50)                  →  history list from cycles table

filter_eligible_instances(state, history)
    └─ exclude protected=true          (tag check only)
    └─ exclude cooldown instances      (Real Action in last 3 cycles)
    └─ returns: eligible_ids list

call_llm(state, eligible_ids, history)
    └─ single Anthropic API call
    └─ returns: raw action list (unvalidated)

validate_actions(llm_actions, state, eligible_ids)
    └─ drop unknown tools
    └─ drop non-eligible targets
    └─ cap real actions at 2
    └─ returns: validated action list

TOOL_DISPATCH[action["tool"]](**action)  ×  M
    └─ boto3 calls (or no-op)
    └─ returns: aws_response dicts

write_cycle_records(records)           →  cycles table
```

---

## Error Handling Strategy

| Location | Error type | Handling |
|---|---|---|
| `observe_all()` | boto3 exception | Propagates to Orchestrator `try/except`; cycle is skipped |
| `call_llm()` | Non-JSON response | Raises `ValueError`; caught by Orchestrator; cycle skipped after persistence of observed data |
| `call_llm()` | Anthropic API error | Propagates; caught by Orchestrator |
| `validate_actions()` | Unknown tool / bad ID | Silently dropped; no exception raised |
| `TOOL_DISPATCH[fn]()` | boto3 exception | Each tool may raise; Orchestrator catches at cycle level |
| `db.py` helpers | SQLAlchemy error | Propagates; Orchestrator catches |
| All above | Any unhandled exception | `logging.exception()` + `continue` — loop never exits |

The Orchestrator's `try/except` wraps the entire cycle body (steps 1–9). Step 10 (`sleep`) is outside the try block, ensuring the interval is always respected even after a failed cycle.

---

## Concurrency Model

All four processes are independent OS-level processes (or threads launched from a supervisor). They share state only through `audit.db`.

| Process | Writes to DB | Reads from DB |
|---|---|---|
| Orchestrator | instances, cycles, (kill_switch via API) | cycles (history), kill_switch |
| API Server | kill_switch (via POST) | instances, cycles, kill_switch |
| Publisher | — | — |
| (DB layer) | managed by SQLAlchemy | managed by SQLAlchemy |

SQLite is accessed with `check_same_thread=False` and SQLAlchemy's default connection pool, which is safe for multi-threaded access to a single SQLite file when using separate `Session` objects per request/operation (as `SessionLocal()` ensures).

For a production deployment these could be separate OS processes launched by a process manager (e.g., `supervisord` or `Procfile`). For development, four terminal tabs each running their respective entry point is sufficient.

---

## Security Notes

- IAM credentials are scoped to the minimum 7 actions (Requirement 1.5).
- All credentials are read from `.env`; never appear in source code.
- `.env` and `audit.db` are in `.gitignore`.
- The LLM is sandboxed: it can only suggest tools from `ALLOWED_TOOLS`; the Validator enforces this hard boundary before any boto3 call is made.
- Protected instances are excluded before the LLM prompt is assembled, so the LLM never receives their IDs in `eligible_ids`.

---

## Correctness Properties

*A property is a characteristic or behavior that should hold true across all valid executions of a system — essentially, a formal statement about what the system should do. Properties serve as the bridge between human-readable specifications and machine-verifiable correctness guarantees.*

### Property 1: Instance Snapshot Round-Trip

*For any* instance data dict written via `upsert_instance_snapshot()`, a subsequent read from the `instances` table SHALL return a record whose fields are equivalent to the written data.

**Validates: Requirements 2.4**

---

### Property 2: Cycle Records Round-Trip

*For any* list of cycle record dicts written via `write_cycle_records()`, all inserted records SHALL subsequently appear in the results of `get_recent_cycles()` when the limit is large enough.

**Validates: Requirements 2.5**

---

### Property 3: Kill Switch State Fidelity

*For any* sequence of toggle operations (setting `active` to `true` or `false`), `is_kill_switch_active()` SHALL return the `active` value of the most recently inserted `kill_switch` row.

**Validates: Requirements 2.6, 11.1, 11.2**

---

### Property 4: Recent Cycles Limit Enforcement

*For any* integer `limit` and any number `N` of cycle records stored, `get_recent_cycles(limit)` SHALL return exactly `min(N, limit)` records ordered by timestamp descending.

**Validates: Requirements 2.7, 3.2**

---

### Property 5: Protected Instance Permanent Exclusion

*For any* state dict containing one or more instances with `tags["protected"] == "true"`, `filter_eligible_instances()` SHALL never include those instance IDs in the returned eligible list, regardless of their name, type, or action history.

**Validates: Requirements 6.2, 6.4**

---

### Property 6: Cooldown Exclusion

*For any* instance that received a Real Action within the last 3 cycle records in `recent_history`, `filter_eligible_instances()` SHALL exclude that instance ID from the returned eligible list.

**Validates: Requirements 6.3**

---

### Property 7: Single LLM Call Per Cycle Invariant

*For any* invocation of `call_llm(state, eligible_ids, history)`, regardless of the size or content of `state`, exactly one call SHALL be made to the Anthropic API.

**Validates: Requirements 7.2**

---

### Property 8: Validator Rejects Unknown and Ineligible Actions

*For any* list of LLM-proposed actions where some have tool names outside `ALLOWED_TOOLS` or target instance IDs outside `eligible_ids`, `validate_actions()` SHALL return only actions with valid tool names AND eligible target IDs.

**Validates: Requirements 8.2, 8.3**

---

### Property 9: Real Action Cap Enforcement

*For any* list of validated actions containing more than 2 Real Actions, `validate_actions()` SHALL return a list containing at most 2 Real Actions; non-real actions (`do_nothing`, `alert_human`) beyond that count are retained unrestricted.

**Validates: Requirements 8.4**

---

### Property 10: Orchestrator Kill Switch Bypass

*For any* orchestrator cycle where `is_kill_switch_active()` returns `True`, `call_llm()` SHALL NOT be called and no entry in `TOOL_DISPATCH` SHALL be invoked during that cycle.

**Validates: Requirements 10.3, 11.3**

---

### Property 11: Orchestrator Cycle Resilience

*For any* exception raised by any single step (1–9) within an orchestrator cycle, the orchestrator loop SHALL catch the exception, log it, and proceed to execute the next cycle without terminating.

**Validates: Requirements 10.4**

---

### Property 12: Publisher Phase-to-Metric Consistency

*For any* cycle count `n`, the metric values published by the Publisher SHALL correspond to the correct phase: phase 0 for `n < 10`, phase 1 for `10 ≤ n < 20`, and phase 2 for `n ≥ 20`.

**Validates: Requirements 4.3**

---

### Property 13: Publisher Dimension Inclusion

*For any* instance name passed to the Publisher, every `put_metric_data` call for that instance SHALL include a CloudWatch dimension `{"Name": "InstanceName", "Value": <instance_name>}`.

**Validates: Requirements 4.5**

---

### Property 14: Observer Metric Coverage

*For any* set of N instances returned by `ec2.describe_instances()`, `observe_all()` SHALL fetch CloudWatch metrics for all N instances (i.e., the number of `get_metric_data` calls scales with N).

**Validates: Requirements 5.3**
