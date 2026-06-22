# Requirements Document

## Introduction

The Cost Optimizer Agent is an autonomous observe–decide–act system that monitors live AWS EC2 instances, invokes an LLM once per cycle to decide what infrastructure action to take, executes real boto3 API calls, persists all decisions and outcomes to a local SQLite database, and exposes everything through a REST API consumed by a terminal dashboard. The system is composed of four concurrently running processes: the orchestrator loop, the synthetic metric publisher, the API server, and the database layer. All Python source files reside in the `Agent/` directory.

## Glossary

- **Agent**: The overall cost-optimizer-agent system described in this document.
- **Orchestrator**: The main control loop that sequences each observe–decide–act cycle.
- **Observer**: The module that reads EC2 instance state and CloudWatch metrics from AWS.
- **Safety Filter**: The module (`safety.py`) that produces the eligible-instance list before any LLM call.
- **LLM**: The Groq llama-3.3-70b-versatile language model used as the sole decision-maker.
- **Validator**: The module that checks LLM output against allowed tools and eligible instances.
- **Executor**: The module that dispatches validated actions to boto3 calls.
- **Publisher**: The synthetic CloudWatch metric publisher (`publisher.py`).
- **API Server**: The FastAPI + uvicorn HTTP server (`main.py` + `api.py`).
- **Database**: The SQLite file `audit.db` managed via SQLAlchemy (`db.py` + `models.py`).
- **Kill Switch**: A database-backed toggle that halts Agent action execution within one cycle.
- **Protected Instance**: An EC2 instance tagged `protected=true`; never acted upon.
- **Eligible Instance**: An EC2 instance that has passed the Safety Filter and may receive an action this cycle.
- **Cycle**: One complete iteration of the Orchestrator loop (observe → filter → decide → validate → execute → persist).
- **POLL_INTERVAL**: Environment variable (seconds) that controls the sleep time between cycles.
- **CostOptimizerAgent**: The CloudWatch metric namespace used by the Publisher.
- **Tool**: One of the six executable actions: `stop_instance`, `start_instance`, `resize_instance`, `tag_instance`, `do_nothing`, `alert_human`.
- **Real Action**: Any Tool other than `do_nothing` or `alert_human`.
- **setup_aws.py**: A runnable boto3 script that provisions all required AWS infrastructure.
- **test_smoke.py**: A lightweight smoke-test script that validates the system without live AWS calls.
- **requirements.txt**: Dependency file with pinned package versions.

---

## Requirements

### Requirement 1: AWS Infrastructure Provisioning

**User Story:** As a DevOps engineer, I want a single script to provision all required AWS resources, so that I can reproduce the environment from scratch without manual steps.

#### Acceptance Criteria

1. THE `setup_aws.py` script SHALL create exactly four EC2 instances of type `t3.micro` named `web-1`, `web-2`, `worker-1`, and `cache-1`.
2. WHEN `setup_aws.py` creates an EC2 instance, THE script SHALL apply the tag `project=cost-optimizer-agent` to that instance.
3. WHEN `setup_aws.py` creates the `cache-1` instance, THE script SHALL also apply the tag `protected=true` to that instance.
4. WHEN `setup_aws.py` creates any instance whose name is not `cache-1`, THE script SHALL apply the tag `protected=false` to that instance.
5. THE `setup_aws.py` script SHALL load existing AWS credentials from `Agent/.env` directly (no IAM user creation).
6. THE `setup_aws.py` script SHALL wait for all launched instances to reach the `running` state before exiting.
7. THE `.env` file SHALL never be committed to version control; THE project SHALL include `.env` in `.gitignore`.

---

### Requirement 2: Database Models and Helper Functions

**User Story:** As a developer, I want a well-structured SQLite database with helper functions, so that all cycle data is persisted and queryable without raw SQL.

#### Acceptance Criteria

1. THE Database SHALL define an `instances` table with columns: `id`, `name`, `instance_type`, `status`, `tags` (JSON string), `last_action`, `last_action_reasoning`, and `updated_at`.
2. THE Database SHALL define a `cycles` table with columns: `id`, `cycle`, `timestamp`, `instance_id`, `instance_name`, `action`, `reasoning`, `validated`, and `aws_response`.
3. THE Database SHALL define a `kill_switch` table with columns: `id`, `active`, and `toggled_at`.
4. THE `db.py` module SHALL expose a `upsert_instance_snapshot(instance_data: dict)` function that inserts or updates a row in the `instances` table.
5. THE `db.py` module SHALL expose a `write_cycle_records(records: list[dict])` function that inserts rows into the `cycles` table.
6. THE `db.py` module SHALL expose an `is_kill_switch_active() -> bool` function that reads the most recent row from `kill_switch` and returns its `active` field.
7. THE `db.py` module SHALL expose a `get_recent_cycles(limit: int) -> list` function that returns the most recent `limit` rows from the `cycles` table ordered by `timestamp` descending.
8. WHEN the Agent starts for the first time and `audit.db` does not exist, THE Database SHALL create `audit.db` and all tables automatically via SQLAlchemy `create_all`.

---

### Requirement 3: API Server

**User Story:** As a dashboard consumer, I want a REST API that exposes instance state, cycle history, cost summary, and kill-switch control, so that the terminal UI can display live data.

#### Acceptance Criteria

1. THE API Server SHALL expose `GET /api/instances` returning current instance snapshots from the `instances` table.
2. THE API Server SHALL expose `GET /api/cycles` accepting an optional `limit` query parameter (default 50) and returning cycle records from the `cycles` table.
3. THE API Server SHALL expose `GET /api/cost` returning a cost summary derived from current instance data.
4. THE API Server SHALL expose `GET /api/killswitch` returning the current kill-switch state.
5. THE API Server SHALL expose `POST /api/killswitch` accepting a JSON body that sets the kill-switch `active` field and persisting the change to the `kill_switch` table.
6. THE API Server SHALL expose `GET /api/metrics` that calls `observe_all()` live and returns current CloudWatch metrics per instance.
7. THE API Server SHALL expose `POST /api/instances/{instance_id}/stop` that manually triggers a stop action for the specified instance.
8. THE API Server SHALL expose `POST /api/instances/{instance_id}/start` that manually triggers a start action for the specified instance.
9. THE API Server SHALL enable CORS for all origins on all routes.
10. WHEN a request is received by the API Server, THE API Server SHALL respond within 2 seconds under normal database load.
11. THE API Server SHALL be launched via `main.py` using uvicorn and SHALL read its configuration from `.env`.

---

### Requirement 4: Synthetic Metric Publisher

**User Story:** As a developer testing the agent without real workloads, I want a publisher that pushes synthetic CloudWatch metrics on a fixed interval, so that there is supplementary metric data in the `CostOptimizerAgent` namespace.

#### Acceptance Criteria

1. THE Publisher SHALL push metrics to the CloudWatch namespace `CostOptimizerAgent`.
2. THE Publisher SHALL push `CpuUtilizationPercent` and `LatencyMs` as distinct metric names.
3. THE Publisher SHALL advance through exactly 3 phases of metric values determined by the current cycle count.
4. THE Publisher SHALL push metrics every 10 seconds regardless of the Orchestrator's POLL_INTERVAL.
5. WHEN the Publisher pushes a metric, THE Publisher SHALL include the instance name as a CloudWatch dimension.
6. THE Publisher SHALL read AWS credentials from `.env` via `python-dotenv`.
7. NOTE: The Publisher is now decoupled from the Observer. The Observer reads AWS/EC2 native metrics (`CPUUtilization`, `DiskReadBytes`, `DiskWriteBytes` from the `AWS/EC2` namespace); the Publisher continues to write to the `CostOptimizerAgent` namespace as a legacy/supplementary publisher. The Publisher discovers instances directly via EC2 at startup rather than calling `observe_all()`.

---

### Requirement 5: Observer

**User Story:** As the Orchestrator, I want a single function call that returns the full current state of all project instances and their metrics, so that each cycle starts with fresh, accurate data.

#### Acceptance Criteria

1. THE Observer SHALL expose an `observe_all() -> dict` function.
2. WHEN `observe_all()` is called, THE Observer SHALL call `ec2.describe_instances()` filtered by the tag `project=cost-optimizer-agent`.
3. WHEN `observe_all()` is called, THE Observer SHALL call `cloudwatch.get_metric_data()` to retrieve the most recent data points for `CPUUtilization`, `DiskReadBytes`, and `DiskWriteBytes` from the `AWS/EC2` namespace, dimensioned by `InstanceId`, with a period of 300 seconds.
4. THE Observer SHALL read AWS credentials and region from `.env` and SHALL configure CloudWatch with `connect_timeout=10`, `read_timeout=20`, and `max_attempts=2`.
5. IF `ec2.describe_instances()` raises a boto3 exception, THEN THE Observer SHALL propagate the exception to the Orchestrator for handling within the cycle's try/except block.
6. IF `cloudwatch.get_metric_data()` raises a boto3 exception for a given instance, THEN THE Observer SHALL return an empty metrics dict for that instance rather than propagating the error.

---

### Requirement 6: Safety Filter

**User Story:** As a system operator, I want a deterministic pre-LLM filter that permanently excludes protected instances and recently-actioned instances, so that the LLM never receives ineligible targets.

#### Acceptance Criteria

1. THE Safety Filter SHALL expose a `filter_eligible_instances(state: dict, recent_history: list) -> list[str]` function.
2. WHEN `filter_eligible_instances` evaluates an instance, THE Safety Filter SHALL exclude any instance whose `protected` tag equals `true` permanently and unconditionally.
3. WHEN `filter_eligible_instances` evaluates an instance, THE Safety Filter SHALL exclude any instance that received a Real Action within the last 3 cycles recorded in `recent_history`.
4. THE Safety Filter SHALL derive the protected status solely from the instance's tag value and SHALL NOT reference any instance name directly.
5. THE Safety Filter SHALL return a list of instance IDs that are eligible for the current cycle.

---

### Requirement 7: LLM Decision Module

**User Story:** As the Orchestrator, I want a single function that sends the current state to the LLM and returns a structured action list, so that all decision logic stays in one place.

#### Acceptance Criteria

1. THE LLM module SHALL expose a `call_llm(state: dict, eligible_ids: list, history: list) -> list[dict]` function.
2. WHEN `call_llm` is invoked, THE LLM module SHALL make exactly one API call to Groq `llama-3.3-70b-versatile` per cycle.
3. THE LLM module SHALL instruct the model to return a JSON array and nothing else.
4. WHEN the LLM response contains markdown code fences, THE LLM module SHALL strip those fences before parsing the JSON.
5. THE LLM module SHALL read the `GROQ_API_KEY` from `.env`.
6. IF the model returns a response that cannot be parsed as a JSON array, THEN THE LLM module SHALL raise a `ValueError` for the Orchestrator's try/except to handle.
7. THE LLM module SHALL send a compact summary prompt — including average CPU%, DiskReadBytes, DiskWriteBytes per instance and the last 10 history records only — rather than the full raw state JSON, in order to minimize token usage.

---

### Requirement 8: Validator

**User Story:** As a safety layer, I want post-LLM validation that rejects unknown tools and caps destructive actions per cycle, so that the LLM cannot exceed safe operation boundaries.

#### Acceptance Criteria

1. THE Validator SHALL expose a `validate_actions(llm_response: list, state: dict, eligible_ids: list) -> list[dict]` function.
2. WHEN `validate_actions` processes an action, THE Validator SHALL reject any action whose tool name is not in the set: `stop_instance`, `start_instance`, `resize_instance`, `tag_instance`, `do_nothing`, `alert_human`.
3. WHEN `validate_actions` processes an action, THE Validator SHALL reject any action targeting an instance ID not present in `eligible_ids`.
4. THE Validator SHALL cap the number of Real Actions per cycle at 2; any Real Actions beyond the first 2 SHALL be dropped.
5. THE Validator SHALL return the filtered and capped list of validated actions.

---

### Requirement 9: Executor

**User Story:** As the Orchestrator, I want a tool-dispatch layer that maps validated action names to boto3 calls, so that execution logic is isolated and testable.

#### Acceptance Criteria

1. THE Executor SHALL implement exactly 6 tools: `stop_instance`, `start_instance`, `resize_instance`, `tag_instance`, `do_nothing`, and `alert_human`.
2. THE Executor SHALL expose a `TOOL_DISPATCH` dictionary mapping each tool name string to its callable.
3. WHEN `stop_instance` is called, THE Executor SHALL call `ec2.stop_instances()` with the provided instance ID.
4. WHEN `start_instance` is called, THE Executor SHALL call `ec2.start_instances()` with the provided instance ID.
5. WHEN `resize_instance` is called, THE Executor SHALL call `ec2.modify_instance_attribute()` to change the instance type.
6. WHEN `tag_instance` is called, THE Executor SHALL call `ec2.create_tags()` with the provided key-value pairs.
7. WHEN `do_nothing` is called, THE Executor SHALL return a no-op response without making any AWS API call.
8. WHEN `alert_human` is called, THE Executor SHALL log the alert message and return a response without making any AWS API call.
9. THE Executor SHALL read AWS credentials from `.env`.

---

### Requirement 10: Orchestrator Loop

**User Story:** As the system operator, I want a resilient main loop that sequences every cycle and never crashes, so that the agent runs continuously without manual intervention.

#### Acceptance Criteria

1. THE Orchestrator SHALL run a continuous loop sleeping `POLL_INTERVAL` seconds between cycles.
2. WHEN a cycle begins, THE Orchestrator SHALL execute the following 10 steps in order: (1) read kill switch, (2) call `observe_all()`, (3) call `upsert_instance_snapshot()` for each instance, (4) call `get_recent_cycles()`, (5) call `filter_eligible_instances()`, (6) call `call_llm()`, (7) call `validate_actions()`, (8) call each validated action via `TOOL_DISPATCH`, (9) call `write_cycle_records()`, (10) sleep.
3. WHILE the Kill Switch is active, THE Orchestrator SHALL skip steps 6 through 8 and SHALL NOT call the LLM or execute any actions.
4. WHEN any step in a cycle raises an exception, THE Orchestrator SHALL catch the exception, log it, and continue to the next cycle without exiting.
5. THE Orchestrator SHALL read `POLL_INTERVAL` from `.env` and SHALL default to 10 seconds if the variable is not set.
6. THE Orchestrator SHALL source all data from live AWS API responses and database reads; THE Orchestrator SHALL contain zero hardcoded instance names, IDs, or metric values.

---

### Requirement 11: Kill Switch

**User Story:** As a system operator, I want a kill switch that halts agent actions within one cycle, so that I can stop the agent immediately without restarting any process.

#### Acceptance Criteria

1. WHEN `POST /api/killswitch` is called with `{"active": true}`, THE Kill Switch SHALL be set to active and persisted in the `kill_switch` table.
2. WHEN `POST /api/killswitch` is called with `{"active": false}`, THE Kill Switch SHALL be set to inactive and persisted in the `kill_switch` table.
3. WHILE the Kill Switch is active, THE Orchestrator SHALL complete the current observe and filter steps, then skip the LLM call and all executor calls for that cycle.
4. WHEN the Kill Switch state changes, THE change SHALL take effect no later than the start of the next cycle.

---

### Requirement 12: Concurrent Process Operation

**User Story:** As a developer, I want all four processes to run simultaneously without interference, so that the agent, publisher, and API server operate independently.

#### Acceptance Criteria

1. THE Agent system SHALL support running the Orchestrator, Publisher, and API Server as four simultaneously executing processes.
2. WHEN the API Server is running, THE API Server SHALL serve requests independently of the Orchestrator cycle timing.
3. WHEN the Publisher is running, THE Publisher SHALL push metrics independently of the Orchestrator cycle timing.
4. THE Database SHALL be the sole shared state between the Orchestrator and the API Server.
5. WHILE multiple processes access `audit.db` concurrently, THE Database SHALL use SQLAlchemy connection pooling or `check_same_thread=False` to avoid threading errors.

---

### Requirement 13: Environment Configuration

**User Story:** As a developer, I want all configuration read from `.env`, so that no credentials or environment-specific values appear in source code.

#### Acceptance Criteria

1. THE Agent system SHALL read `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_DEFAULT_REGION`, `GROQ_API_KEY`, `DB_URL`, `POLL_INTERVAL`, and optionally `API_BASE_URL` exclusively from `.env` via `python-dotenv`.
2. THE Agent system SHALL contain zero hardcoded credential values, instance names, instance IDs, region strings, or API keys in any Python source file.
3. IF a required environment variable is missing at startup, THEN THE affected module SHALL raise a clear `EnvironmentError` or `KeyError` identifying the missing variable.
4. THE `.env` file SHALL be listed in `.gitignore` and SHALL never be committed to version control.
5. ALL modules SHALL load `.env` using `load_dotenv(dotenv_path=Path(__file__).resolve().parent / ".env")` pointing to `Agent/.env`.

---

### Requirement 14: Dependency Management

**User Story:** As a developer, I want a `requirements.txt` with pinned versions, so that the environment is exactly reproducible.

#### Acceptance Criteria

1. THE project SHALL include a `requirements.txt` file in the `Agent/` directory listing all runtime dependencies with exact pinned versions (using `==`).
2. THE `requirements.txt` SHALL include at minimum: `boto3`, `groq`, `httpx`, `fastapi`, `uvicorn`, `sqlalchemy`, `pydantic`, `python-dotenv`, `PyQt6`, `pyqtgraph`.
3. WHEN a developer runs `pip install -r requirements.txt`, THE installation SHALL complete without version conflicts.

---

### Requirement 15: Smoke Tests

**User Story:** As a developer, I want a lightweight smoke test that validates module imports and basic logic without live AWS calls, so that I can confirm the codebase is intact quickly.

#### Acceptance Criteria

1. THE project SHALL include a `test_smoke.py` file in the `Agent/` directory.
2. WHEN `test_smoke.py` is executed, THE script SHALL import all Agent modules without raising `ImportError`.
3. WHEN `test_smoke.py` tests `filter_eligible_instances`, THE script SHALL verify that an instance tagged `protected=true` is excluded from the returned list without making any AWS API call.
4. WHEN `test_smoke.py` tests the Validator, THE script SHALL verify that an unknown tool name is rejected and that the Real Action cap of 2 is enforced.
5. WHEN `test_smoke.py` tests `is_kill_switch_active`, THE script SHALL verify the function returns a boolean without connecting to a live database by using an in-memory SQLite database.
6. WHEN all checks in `test_smoke.py` pass, THE script SHALL print a summary line confirming all tests passed and SHALL exit with code 0.
7. WHEN any check in `test_smoke.py` fails, THE script SHALL print the failing assertion and SHALL exit with code 1.

---

### Requirement 16: Terminal Dashboard (PyQt6 GUI)

**User Story:** As a system operator, I want a graphical desktop dashboard that displays live instance state, cycle history, cost metrics, and a kill switch control, so that I can monitor and manage the agent at a glance.

#### Acceptance Criteria

1. THE Dashboard SHALL be implemented as a PyQt6 `QMainWindow` application in `Agent/dashboard.py`.
2. THE Dashboard SHALL present a `QTabWidget` with exactly three tabs: **Instances**, **Cycles**, and **Metrics & Cost**.
3. THE **Instances** tab SHALL show a `QTableWidget` with columns: `Name`, `Type`, `Status`, `Protected`, `Last Action`, `CPU Utilization`, `Disk Read Bytes`, `Disk Write Bytes`, `Updated At`; rows for running instances SHALL be highlighted green, stopped instances red.
4. THE **Cycles** tab SHALL show a `QTableWidget` with the last 50 cycle records, including columns: `Cycle #`, `Timestamp`, `Instance`, `Action`, `Reasoning`, `Validated`, `AWS Response`.
5. THE **Metrics & Cost** tab SHALL display cost summary cards (`Total Instances`, `Running`, `Stopped`, `Est. Hourly USD`) and two live `pyqtgraph` line charts — one for CPU% per instance and one for Latency ms per instance — each rolling the last 20 data points.
6. THE Dashboard SHALL include a kill switch `QPushButton` in the toolbar that turns red when active and green when inactive, toggling via `POST /api/killswitch`.
7. THE Dashboard SHALL auto-refresh all tabs every 3 seconds using `QTimer`.
8. THE Dashboard SHALL perform all API calls using `httpx` (sync) — no direct DB or boto3 imports.
9. THE Dashboard SHALL read the API base URL from the `API_BASE_URL` environment variable (default `http://localhost:8000`).
10. THE Dashboard SHALL use a background `QThread` for non-blocking API polling so the UI remains responsive.
