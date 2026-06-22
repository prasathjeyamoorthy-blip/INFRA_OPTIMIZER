# Implementation Plan: Cost Optimizer Agent

## Overview

Build an autonomous observe–decide–act loop that monitors live AWS EC2 instances, calls the Groq LLM (llama-3.3-70b-versatile) once per cycle to decide what infrastructure action to take, executes real boto3 calls, persists all decisions to SQLite, and exposes a REST API. All source files live in `Agent/`. Tasks are ordered foundation-first: scaffolding → data layer → API server → observer → safety filter → LLM module → validator → executor → orchestrator → publisher → AWS provisioning → smoke tests → integration verification.

---

## Tasks

### 1. Project Scaffolding

- [x] 1.1 Create `.gitignore` in the project root
  - Add entries: `.env`, `audit.db`, `__pycache__/`, `*.pyc`, `*.pyo`, `.pytest_cache/`
  - Ensures credentials and the database file are never committed
  - _Requirements: 1.7, 13.4_

- [x] 1.2 Create `.env.example` in `Agent/`
  - Include placeholder keys: `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_DEFAULT_REGION=us-east-1`, `GROQ_API_KEY`, `DB_URL=sqlite:///./audit.db`, `POLL_INTERVAL=480`, `API_HOST=0.0.0.0`, `API_PORT=8000`, `API_BASE_URL=http://localhost:8000`
  - Must never contain real credentials
  - _Requirements: 13.1, 13.2_

- [x] 1.3 Create `Agent/requirements.txt` with pinned versions
  - Include: `boto3==1.34.69`, `groq==0.11.0`, `httpx==0.27.2`, `fastapi==0.110.1`, `uvicorn==0.29.0`, `sqlalchemy==2.0.29`, `pydantic==2.6.4`, `python-dotenv==1.0.1`, `PyQt6==6.7.0`, `pyqtgraph==0.13.7`
  - Verify `pip install -r requirements.txt` resolves without conflicts
  - _Requirements: 14.1, 14.2, 14.3_

---

### 2. Database Layer

- [x] 2.1 Implement `Agent/models.py` — SQLAlchemy ORM table definitions
  - Define `Base = declarative_base()`
  - Define `Instance` model: columns `id` (String PK), `name`, `instance_type`, `status`, `tags` (String/JSON), `last_action` (nullable), `last_action_reasoning` (nullable), `updated_at` (DateTime with `onupdate`)
  - Define `Cycle` model: columns `id` (Integer autoincrement PK), `cycle`, `timestamp` (DateTime default now), `instance_id`, `instance_name`, `action`, `reasoning`, `validated` (Boolean), `aws_response` (String/JSON)
  - Define `KillSwitch` model: columns `id` (Integer autoincrement PK), `active` (Boolean non-null), `toggled_at` (DateTime default now)
  - _Requirements: 2.1, 2.2, 2.3_

- [x] 2.2 Implement `Agent/db.py` — session factory and helper functions
  - Load `DB_URL` from environment via `python-dotenv`; raise `KeyError` if missing
  - Create SQLAlchemy engine with `connect_args={"check_same_thread": False}`
  - Create `SessionLocal = sessionmaker(bind=engine)`
  - Implement `init_db()`: calls `Base.metadata.create_all(engine)` — auto-creates `audit.db` on first run
  - Implement `upsert_instance_snapshot(instance_data: dict)`: insert or update row in `instances` keyed on `instance_data["id"]`
  - Implement `write_cycle_records(records: list[dict])`: bulk-insert rows into `cycles`
  - Implement `is_kill_switch_active() -> bool`: reads most recent `kill_switch` row; returns `False` if table is empty
  - Implement `get_recent_cycles(limit: int = 50) -> list[dict]`: returns most recent `limit` rows from `cycles` ordered by `timestamp` DESC
  - _Requirements: 2.4, 2.5, 2.6, 2.7, 2.8, 12.4, 12.5_

- [x] 2.3 Write property test for `upsert_instance_snapshot` round-trip
  - **Property 1: Instance Snapshot Round-Trip** — for any instance data dict written via `upsert_instance_snapshot()`, a subsequent read from the `instances` table returns a record with equivalent fields
  - Use an in-memory SQLite DB (`DB_URL=sqlite:///:memory:`)
  - **Validates: Requirements 2.4**

- [x]* 2.4 Write property test for `write_cycle_records` / `get_recent_cycles` round-trip
  - **Property 2: Cycle Records Round-Trip** — for any list of cycle record dicts written via `write_cycle_records()`, all inserted records appear in `get_recent_cycles()` when the limit is large enough
  - **Validates: Requirements 2.5**

- [x]* 2.5 Write property test for kill switch state fidelity
  - **Property 3: Kill Switch State Fidelity** — for any sequence of toggle operations, `is_kill_switch_active()` returns the `active` value of the most recently inserted row
  - **Validates: Requirements 2.6, 11.1, 11.2**

- [x]* 2.6 Write property test for `get_recent_cycles` limit enforcement
  - **Property 4: Recent Cycles Limit Enforcement** — for any integer `limit` and any `N` records stored, `get_recent_cycles(limit)` returns exactly `min(N, limit)` records ordered by timestamp descending
  - **Validates: Requirements 2.7, 3.2**

- [x] 2.7 Checkpoint — database layer
  - Run `python -c "from db import init_db; init_db()"` (with `DB_URL=sqlite:///:memory:` set) and confirm no errors
  - Ensure all tests pass, ask the user if questions arise.


---

### 3. API Server

- [x] 3.1 Implement `Agent/api.py` — Pydantic schemas and FastAPI route handlers
  - Define Pydantic schemas: `InstanceSchema`, `CycleSchema`, `CostSummarySchema`, `KillSwitchSchema`
  - `CostSummarySchema` fields: `total_instances`, `running_count`, `stopped_count`, `estimated_hourly_usd`
  - `KillSwitchSchema` fields: `active` (bool), `toggled_at` (datetime | None)
  - Create `router = APIRouter(prefix="/api")`
  - Implement `GET /api/instances` → query `instances` table, return `list[InstanceSchema]`
  - Implement `GET /api/cycles` with optional `limit: int = 50` → call `get_recent_cycles(limit)`, return `list[CycleSchema]`
  - Implement `GET /api/cost` → derive `running_count`, `stopped_count`, and `estimated_hourly_usd` from instance data; return `CostSummarySchema`
  - Implement `GET /api/killswitch` → read most recent kill_switch row, return `KillSwitchSchema`
  - Implement `POST /api/killswitch` → accept `{"active": bool}` body, insert new `KillSwitch` row, return updated `KillSwitchSchema`
  - Implement `GET /api/metrics` → call `observe_all()` live and return current CloudWatch metrics per instance
  - Implement `POST /api/instances/{instance_id}/stop` → manually stop the specified instance via boto3
  - Implement `POST /api/instances/{instance_id}/start` → manually start the specified instance via boto3
  - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7, 3.8_

- [x] 3.2 Implement `Agent/main.py` — FastAPI app entry point
  - Create `FastAPI` app with `title="Cost Optimizer Agent API"`
  - Add `CORSMiddleware` with `allow_origins=["*"]`, `allow_methods=["*"]`, `allow_headers=["*"]`
  - Include the router from `api.py`
  - Register `@app.on_event("startup")` handler that calls `init_db()`
  - Add `if __name__ == "__main__":` block launching `uvicorn.run()` with `API_HOST` and `API_PORT` read from environment (defaults `0.0.0.0` / `8000`)
  - _Requirements: 3.6, 3.8, 13.1_


---

### 4. Observer

- [x] 4.1 Implement `Agent/observer.py` — AWS state reader
  - Load AWS credentials and `AWS_DEFAULT_REGION` from `.env` via `python-dotenv`; raise `EnvironmentError` if any required key is missing
  - Build boto3 `ec2` client and a boto3 `cloudwatch` client configured with `botocore.config.Config(connect_timeout=10, read_timeout=20, retries={"max_attempts": 2})`
  - Implement `observe_all() -> dict`:
    - Call `ec2.describe_instances(Filters=[{"Name": "tag:project", "Values": ["cost-optimizer-agent"]}])`
    - For each discovered instance, extract: `id`, `name` (from Name tag), `instance_type`, `status`, `tags` (as dict)
    - Call `cloudwatch.get_metric_data()` for each instance requesting `CPUUtilization`, `DiskReadBytes`, and `DiskWriteBytes` from the `AWS/EC2` namespace, dimensioned by `InstanceId`, with a 300-second period
    - If CloudWatch raises for a given instance, return an empty metrics dict `{}` for that instance (do not propagate)
    - Return `{"instances": [...]}` dict
    - Propagate any EC2 boto3 exception unhandled to the caller
  - _Requirements: 5.1, 5.2, 5.3, 5.4, 5.5, 5.6_

- [ ]* 4.2 Write property test for observer metric coverage
  - **Property 14: Observer Metric Coverage** — for any set of N instances returned by a mock `ec2.describe_instances()`, `observe_all()` fetches CloudWatch metrics for all N instances
  - Mock boto3 clients using `unittest.mock.patch`
  - **Validates: Requirements 5.3**


---

### 5. Safety Filter

- [x] 5.1 Implement `Agent/safety.py` — pre-LLM eligibility filter
  - Define constants: `REAL_ACTIONS = {"stop_instance", "start_instance", "resize_instance", "tag_instance"}` and `COOLDOWN_CYCLES = 3`
  - Implement `filter_eligible_instances(state: dict, recent_history: list) -> list[str]`:
    - Rule 1 (permanent exclusion): exclude any instance where `instance["tags"].get("protected") == "true"` — **never** compare against instance name strings
    - Rule 2 (cooldown exclusion): exclude any instance that received a Real Action in the last `COOLDOWN_CYCLES` cycle records in `recent_history` (check `record["action"]` and `record["instance_id"]`)
    - Return list of eligible instance IDs
  - Function must be pure: no database or AWS calls inside
  - _Requirements: 6.1, 6.2, 6.3, 6.4, 6.5_

- [ ]* 5.2 Write property test for protected instance permanent exclusion
  - **Property 5: Protected Instance Permanent Exclusion** — for any state dict containing instances with `tags["protected"] == "true"`, `filter_eligible_instances()` never includes those IDs regardless of name, type, or action history
  - **Validates: Requirements 6.2, 6.4**

- [ ]* 5.3 Write property test for cooldown exclusion
  - **Property 6: Cooldown Exclusion** — for any instance that received a Real Action within the last 3 cycle records in `recent_history`, `filter_eligible_instances()` excludes that instance ID
  - **Validates: Requirements 6.3**

- [x] 5.4 Checkpoint — safety filter
  - Ensure all tests pass, ask the user if questions arise.


---

### 6. LLM Module

- [x] 6.1 Implement `Agent/llm.py` — Groq decision module
  - Load `GROQ_API_KEY` from `.env` via `python-dotenv`; raise `EnvironmentError` if missing
  - Implement `call_llm(state: dict, eligible_ids: list[str], history: list[dict]) -> list[dict]`:
    - Build a compact summary prompt: compute average `CPUUtilization`, `DiskReadBytes`, `DiskWriteBytes` per instance from `state`; include only the last 10 records from `history`
    - Build the prompt string with: system instruction, `ELIGIBLE INSTANCE IDs` section listing `eligible_ids`, `CURRENT STATE SUMMARY` section with the compact per-instance summary, `RECENT ACTION HISTORY` section with the last 10 history records, and instruction to respond with ONLY a valid JSON array where each element has `tool`, `instance_id`, `reasoning`
    - Make exactly one call to `groq.Groq().chat.completions.create()` using model `llama-3.3-70b-versatile`
    - Strip leading/trailing whitespace and markdown code fences (` ```json ` / ` ``` `) from the response text
    - Parse with `json.loads()`; if parsing fails raise `ValueError` with the raw response text included in the message
    - Return the parsed list of action dicts
  - _Requirements: 7.1, 7.2, 7.3, 7.4, 7.5, 7.6, 7.7_

- [ ]* 6.2 Write property test for single LLM call invariant
  - **Property 7: Single LLM Call Per Cycle Invariant** — for any invocation of `call_llm()`, exactly one call is made to the Groq API regardless of state size
  - Mock the Groq client; count calls
  - **Validates: Requirements 7.2**


---

### 7. Validator

- [x] 7.1 Implement `Agent/validator.py` — post-LLM safety rails
  - Define constants:
    - `ALLOWED_TOOLS = {"stop_instance", "start_instance", "resize_instance", "tag_instance", "do_nothing", "alert_human"}`
    - `REAL_ACTIONS = {"stop_instance", "start_instance", "resize_instance", "tag_instance"}`
    - `MAX_REAL_ACTIONS_PER_CYCLE = 2`
  - Implement `validate_actions(llm_response: list[dict], state: dict, eligible_ids: list[str]) -> list[dict]`:
    - Rule 1: drop any action whose `"tool"` is not in `ALLOWED_TOOLS`
    - Rule 2: drop any action whose `"instance_id"` is not in `eligible_ids`
    - Rule 3: after rules 1–2, count Real Actions; keep first 2 real actions, drop the rest; `do_nothing` and `alert_human` are not counted against the cap and are retained unrestricted
    - Return filtered + capped list (no exceptions raised — invalid actions are silently dropped)
  - Function must be pure: no database or AWS calls
  - _Requirements: 8.1, 8.2, 8.3, 8.4, 8.5_

- [ ]* 7.2 Write property test for validator rejects unknown and ineligible actions
  - **Property 8: Validator Rejects Unknown and Ineligible Actions** — for any list where some actions have tool names outside `ALLOWED_TOOLS` or target IDs outside `eligible_ids`, `validate_actions()` returns only actions with valid tool names AND eligible target IDs
  - **Validates: Requirements 8.2, 8.3**

- [ ]* 7.3 Write property test for real action cap enforcement
  - **Property 9: Real Action Cap Enforcement** — for any list of validated actions containing more than 2 Real Actions, `validate_actions()` returns at most 2 Real Actions; non-real actions beyond the cap are retained
  - **Validates: Requirements 8.4**

- [x] 7.4 Checkpoint — validator
  - Ensure all tests pass, ask the user if questions arise.


---

### 8. Executor

- [x] 8.1 Implement `Agent/executor.py` — boto3 tool dispatch
  - Load AWS credentials from `.env` via `python-dotenv`; raise `EnvironmentError` if missing
  - Build a module-level boto3 `ec2` client using environment credentials
  - Implement all 6 tool functions:
    - `stop_instance(instance_id: str, **kwargs) -> dict`: calls `ec2.stop_instances(InstanceIds=[instance_id])`; returns response dict
    - `start_instance(instance_id: str, **kwargs) -> dict`: calls `ec2.start_instances(InstanceIds=[instance_id])`; returns response dict
    - `resize_instance(instance_id: str, new_type: str, **kwargs) -> dict`: calls `ec2.modify_instance_attribute(InstanceId=instance_id, Attribute="instanceType", Value=new_type)`; returns response dict
    - `tag_instance(instance_id: str, tags: dict, **kwargs) -> dict`: calls `ec2.create_tags(Resources=[instance_id], Tags=[{"Key": k, "Value": v} for k,v in tags.items()])`; returns response dict
    - `do_nothing(instance_id: str, **kwargs) -> dict`: makes no AWS call; returns `{"status": "no_op"}`
    - `alert_human(instance_id: str, message: str, **kwargs) -> dict`: calls `logging.warning(message)`; makes no AWS call; returns `{"status": "alerted", "message": message}`
  - Define `TOOL_DISPATCH: dict[str, Callable]` mapping each tool name string to its callable
  - _Requirements: 9.1, 9.2, 9.3, 9.4, 9.5, 9.6, 9.7, 9.8, 9.9_


---

### 9. Orchestrator

- [x] 9.1 Implement `Agent/orchestrator.py` — main observe–decide–act loop
  - Load `POLL_INTERVAL` from `.env` via `python-dotenv`; default to `10` if not set
  - Import and use: `init_db`, `is_kill_switch_active`, `upsert_instance_snapshot`, `get_recent_cycles`, `write_cycle_records` from `db`; `observe_all` from `observer`; `filter_eligible_instances` from `safety`; `call_llm` from `llm`; `validate_actions` from `validator`; `TOOL_DISPATCH` from `executor`
  - Implement `_name_for(state: dict, instance_id: str) -> str`: looks up instance name from `state["instances"]` by ID — zero hardcoded names
  - Implement `run()` function:
    - Call `init_db()` at startup
    - Enter `while True:` loop with cycle counter
    - Wrap steps 1–9 in a single `try/except Exception`; log the exception and `continue` on failure — loop must never exit
    - Step 1: `kill_active = is_kill_switch_active()`
    - Step 2: `state = observe_all()`
    - Step 3: `upsert_instance_snapshot(inst)` for each instance in `state["instances"]`
    - Step 4: `history = get_recent_cycles(limit=50)`
    - Step 5: `eligible_ids = filter_eligible_instances(state, history)`
    - Steps 6–8: only if `not kill_active` — call `call_llm()`, `validate_actions()`, then dispatch each validated action via `TOOL_DISPATCH[action["tool"]](**action)` and build `records` list
    - Step 9: call `write_cycle_records(records)` if `records` is non-empty
    - Step 10 (outside try): `time.sleep(poll_interval)`
  - Zero hardcoded instance names, IDs, metric values, or region strings anywhere in the file
  - _Requirements: 10.1, 10.2, 10.3, 10.4, 10.5, 10.6, 11.3, 11.4_

- [ ]* 9.2 Write property test for orchestrator kill switch bypass
  - **Property 10: Orchestrator Kill Switch Bypass** — for any cycle where `is_kill_switch_active()` returns `True`, `call_llm()` is not called and no entry in `TOOL_DISPATCH` is invoked
  - Mock all dependencies; set kill switch active; run one cycle; assert LLM and executor are not called
  - **Validates: Requirements 10.3, 11.3**

- [ ]* 9.3 Write property test for orchestrator cycle resilience
  - **Property 11: Orchestrator Cycle Resilience** — for any exception raised by any single step (1–9) within a cycle, the orchestrator loop catches the exception, logs it, and proceeds to the next cycle without terminating
  - Inject faults at each step via mocks; assert the loop continues
  - **Validates: Requirements 10.4**

- [x] 9.4 Checkpoint — orchestrator integration
  - Ensure all tests pass, ask the user if questions arise.


---

### 10. Publisher

- [x] 10.1 Implement `Agent/publisher.py` — synthetic CloudWatch metric publisher
  - Load AWS credentials from `.env` via `python-dotenv`; raise `EnvironmentError` if missing
  - Define constants:
    - `NAMESPACE = "CostOptimizerAgent"`
    - `METRICS = ["CpuUtilizationPercent", "LatencyMs"]`
    - `PUBLISH_INTERVAL = 10` (fixed, not read from `POLL_INTERVAL`)
    - `PHASES` list of 3 dicts: phase 0 (`CpuUtilizationPercent: 15.0, LatencyMs: 80.0`), phase 1 (`55.0, 200.0`), phase 2 (`85.0, 450.0`)
  - Implement `_current_phase(cycle: int) -> dict`: returns phase 0 if `cycle < 10`, phase 1 if `cycle < 20`, phase 2 otherwise
  - Implement `run(instance_names: list[str])`:
    - Build a boto3 `cloudwatch` client
    - Enter `while True:` loop with cycle counter
    - Each iteration: call `_current_phase(cycle)`, then for each instance name and each metric, call `cw.put_metric_data()` with `Namespace=NAMESPACE`, `MetricData` containing `MetricName`, `Dimensions=[{"Name": "InstanceName", "Value": name}]`, `Value`, `Unit="None"`
    - Increment cycle; `time.sleep(PUBLISH_INTERVAL)`
  - Instance names are passed in at runtime — no hardcoded names inside the module
  - **Note:** The Publisher is now optional/legacy. The Observer reads `AWS/EC2` native metrics (`CPUUtilization`, `DiskReadBytes`, `DiskWriteBytes`) directly — it no longer reads from the `CostOptimizerAgent` namespace. The Publisher's `if __name__ == "__main__":` entry point discovers instances directly via EC2 (not by calling `observe_all()`)
  - _Requirements: 4.1, 4.2, 4.3, 4.4, 4.5, 4.6, 4.7_

- [ ]* 10.2 Write property test for publisher phase-to-metric consistency
  - **Property 12: Publisher Phase-to-Metric Consistency** — for any cycle count `n`, `_current_phase(n)` returns phase 0 values for `n < 10`, phase 1 values for `10 ≤ n < 20`, and phase 2 values for `n ≥ 20`
  - Pure function test; no mocks required
  - **Validates: Requirements 4.3**

- [ ]* 10.3 Write property test for publisher dimension inclusion
  - **Property 13: Publisher Dimension Inclusion** — for any instance name passed to `run()`, every `put_metric_data` call includes a CloudWatch dimension `{"Name": "InstanceName", "Value": <instance_name>}`
  - Mock the CloudWatch client; capture all calls; assert dimension presence
  - **Validates: Requirements 4.5**


---

### 11. AWS Provisioning Script

- [x] 11.1 Implement `Agent/setup_aws.py` — one-time AWS infrastructure provisioner
  - Load AWS credentials from `Agent/.env` directly at script start (uses existing credentials — no IAM user creation)
  - Launch exactly 4 `t3.micro` EC2 instances: `web-1`, `web-2`, `worker-1`, `cache-1`
  - Apply tag `project=cost-optimizer-agent` to all four instances immediately after launch
  - Apply tag `protected=true` to `cache-1`; apply `protected=false` to `web-1`, `web-2`, `worker-1`
  - Wait for all instances to reach the `running` state before exiting
  - Print a summary of all created resources on success
  - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.7_


---

### 12. Smoke Tests

- [ ] 12.1 Implement `Agent/test_smoke.py` — lightweight validation without live AWS calls
  - Import all Agent modules in a `try/except ImportError` block; fail with message and `sys.exit(1)` if any import fails
  - Test `filter_eligible_instances` — protected exclusion:
    - Build a mock `state` dict with one instance tagged `protected=true`
    - Call `filter_eligible_instances(state, [])` with empty history
    - Assert the instance ID is NOT in the returned list
  - Test `filter_eligible_instances` — name-agnostic protection:
    - Pass an instance with `protected=true` tag but an unusual/unexpected name (e.g., `"web-99"`)
    - Assert that instance is still excluded, confirming no name-based logic exists
  - Test `validate_actions` — unknown tool rejection:
    - Build an action dict with `tool="hack_aws"` targeting a valid eligible ID
    - Call `validate_actions([action], state, eligible_ids)` and assert the result is empty
  - Test `validate_actions` — real action cap:
    - Build a list of 5 actions all using `tool="stop_instance"` with eligible IDs
    - Call `validate_actions()` and assert `len(result) == 2`
  - Test `is_kill_switch_active` — in-memory DB:
    - Set `DB_URL=sqlite:///:memory:` in environment
    - Call `init_db()` then insert a `KillSwitch(active=True)` row
    - Assert `is_kill_switch_active()` returns `True` and is of type `bool`
  - On all tests passing: print `"All smoke tests passed."` and call `sys.exit(0)`
  - On any failure: print the failing assertion message and call `sys.exit(1)`
  - _Requirements: 15.1, 15.2, 15.3, 15.4, 15.5, 15.6, 15.7_

- [ ] 12.2 Run smoke tests and fix any failures
  - Execute `python Agent/test_smoke.py` (with `DB_URL=sqlite:///:memory:` set in environment)
  - Resolve any `ImportError`, assertion error, or logic bug surfaced
  - Confirm exit code is `0` before proceeding
  - _Requirements: 15.6, 15.7_


---

### 13. Integration Verification

- [ ] 13.1 Wire up concurrent process entry points
  - Verify `main.py` can be started as an independent process: `python Agent/main.py`
  - Verify `orchestrator.py` has a `if __name__ == "__main__": run()` guard so it can be started independently
  - Verify `publisher.py` has a `if __name__ == "__main__":` block that calls `observe_all()` once to get instance names then calls `run(instance_names)`
  - Confirm all four processes (API server, orchestrator, publisher, and any future process) read exclusively from `.env` and share state only via `audit.db`
  - _Requirements: 12.1, 12.2, 12.3, 12.4_

- [ ] 13.2 Validate environment variable coverage across all modules
  - Search all Python files in `Agent/` and confirm zero hardcoded values for: AWS region strings, instance names, instance IDs, API keys, or numeric credentials
  - Confirm every module loads its required env vars via `python-dotenv` at import time or function call
  - Confirm every missing-variable path raises `EnvironmentError` or `KeyError` with a descriptive message
  - _Requirements: 13.1, 13.2, 13.3_

- [ ] 13.3 Final checkpoint — full system
  - Ensure all smoke tests pass: `python Agent/test_smoke.py`
  - Review `Agent/` directory for any orphaned files not referenced by any module
  - Ensure all tests pass, ask the user if questions arise.

---

### 14. Terminal Dashboard (PyQt6 GUI)

- [ ] 14.1 Implement `Agent/dashboard.py` — PyQt6 GUI dashboard
  - `PyQt6==6.7.0` and `pyqtgraph==0.13.7` are included in `Agent/requirements.txt`
  - Create a `CostOptimizerDashboard` class subclassing `QMainWindow`
  - Window title: "Cost Optimizer Agent — Live Dashboard"; default size: 1400x800
  - Use `QTabWidget` with three tabs:
    - **Tab 1 — Instances**: `QTableWidget` showing all instances with columns: `Name`, `Type`, `Status`, `Protected`, `Last Action`, `CPU Utilization`, `Disk Read Bytes`, `Disk Write Bytes`, `Updated At`; running rows highlighted green, stopped rows red
    - **Tab 2 — Cycles**: `QTableWidget` showing last 50 cycle records with columns: `Cycle #`, `Timestamp`, `Instance`, `Action`, `Reasoning`, `Validated`, `AWS Response`
    - **Tab 3 — Metrics & Cost**: split into two sections:
      - Top: cost summary cards showing `Total Instances`, `Running`, `Stopped`, `Est. Hourly USD` as large bold `QLabel` widgets in a `QHBoxLayout`
      - Bottom: two live `pyqtgraph` `PlotWidget` charts — one for CPU utilization per instance (line chart, last 20 data points) and one for Latency ms per instance (line chart, last 20 data points); each instance gets its own colored line
  - Kill switch control: a `QGroupBox` in the main toolbar area showing current state and a `QPushButton` to toggle via `POST /api/killswitch`; button turns red when active, green when inactive
  - Status bar (`QStatusBar`): shows last poll time, instance count, and API errors in red
  - Auto-refresh every 3 seconds using `QTimer`; API calls run in a background `QThread` to keep UI responsive
  - All API base URL read from environment variable `API_BASE_URL` (default `http://localhost:8000`)
  - Use `httpx` (sync) for all API calls — no direct DB or boto3 imports
  - Maintain a rolling history of up to 20 data points per instance for chart plotting
  - Add `if __name__ == "__main__":` guard
  - _Requirements: 16.1, 16.2, 16.3, 16.4, 16.5, 16.6, 16.7, 16.8, 16.9, 16.10_

- [ ] 14.2 Checkpoint — dashboard
  - Install: `uv pip install -r requirements.txt`
  - Run `python Agent/dashboard.py` and verify all three tabs render with live data
  - Confirm CPU and Latency charts update every 3 seconds with rolling data
  - Confirm kill switch button changes color and state correctly
  - Ensure all tests pass, ask the user if questions arise.

---

## Notes

- Tasks marked with `*` are optional and can be skipped for a faster MVP
- All source files live in `Agent/` — the project root holds only `.gitignore`, `.env` (gitignored), and `Agent/`
- The LLM is the **only** decision-maker; no `if/else` logic in any Python file may decide which action to apply to an instance
- `cache-1` is protected exclusively via the `protected=true` tag check in `safety.py` — its name is never hardcoded
- The orchestrator loop must never exit; every cycle is wrapped in `try/except` + `continue`
- Property tests use in-memory SQLite (`sqlite:///:memory:`) or mocked boto3/Groq clients — no live AWS calls required
- Checkpoints validate incremental progress before moving to the next layer


## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1", "1.2", "1.3"] },
    { "id": 1, "tasks": ["2.1"] },
    { "id": 2, "tasks": ["2.2"] },
    { "id": 3, "tasks": ["2.3", "2.4", "2.5", "2.6"] },
    { "id": 4, "tasks": ["3.1", "4.1", "5.1", "6.1", "7.1", "8.1", "10.1"] },
    { "id": 5, "tasks": ["3.2", "4.2", "5.2", "5.3", "6.2", "7.2", "7.3", "10.2", "10.3"] },
    { "id": 6, "tasks": ["9.1"] },
    { "id": 7, "tasks": ["9.2", "9.3"] },
    { "id": 8, "tasks": ["11.1"] },
    { "id": 9, "tasks": ["12.1"] },
    { "id": 10, "tasks": ["12.2"] },
    { "id": 11, "tasks": ["13.1", "13.2"] },
    { "id": 12, "tasks": ["13.3"] },
    { "id": 13, "tasks": ["14.1"] },
    { "id": 14, "tasks": ["14.2"] }
  ]
}
```
