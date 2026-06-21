# Project Structure

```
.
├── .env                    # secrets — never commit (gitignored)
├── .gitignore              # excludes .env, audit.db, __pycache__, etc.
└── Agent/
    ├── requirements.txt    # pinned dependencies
    ├── .env.example        # template — safe to commit
    ├── models.py           # SQLAlchemy ORM: Instance, Cycle, KillSwitch tables
    ├── db.py               # session factory + helper functions
    ├── observer.py         # observe_all() — EC2 + CloudWatch reads
    ├── safety.py           # filter_eligible_instances() — pre-LLM filter
    ├── llm.py              # call_llm() — single Claude API call per cycle
    ├── validator.py        # validate_actions() — post-LLM safety rails
    ├── executor.py         # 6 tool functions + TOOL_DISPATCH dict
    ├── orchestrator.py     # main loop — sequences every cycle
    ├── api.py              # FastAPI route handlers + Pydantic schemas
    ├── main.py             # FastAPI app + uvicorn entry point
    ├── publisher.py        # synthetic CloudWatch metric publisher
    ├── setup_aws.py        # one-time AWS provisioning script
    ├── test_smoke.py       # lightweight smoke tests (no live AWS)
    └── audit.db            # SQLite — auto-created on first run (gitignored)
```

## Module responsibilities

| File | Role |
|---|---|
| `models.py` | Defines the 3 SQLAlchemy tables only — no logic |
| `db.py` | All database reads/writes; exposes 5 helper functions |
| `observer.py` | Reads live EC2 state + CloudWatch metrics; no DB access |
| `safety.py` | Pure function; no DB or AWS calls — determines eligible instances |
| `llm.py` | Pure Claude interface; builds prompt + parses response |
| `validator.py` | Pure function; enforces tool whitelist + real-action cap |
| `executor.py` | Maps tool names to boto3 calls via `TOOL_DISPATCH` dict |
| `orchestrator.py` | Sequences all modules in a 10-step cycle loop |
| `api.py` + `main.py` | REST API for the dashboard; reads DB only |
| `publisher.py` | Independent process; pushes synthetic metrics to CloudWatch |
| `setup_aws.py` | Run once to provision EC2 instances and IAM user |
| `test_smoke.py` | Validates imports and core logic without live AWS |

## Four concurrent processes
1. `python Agent/main.py` — API server (port 8000)
2. `python Agent/orchestrator.py` — observe–decide–act loop
3. `python Agent/publisher.py` — CloudWatch metric publisher
4. Dashboard (external consumer, already implemented)

Shared state between processes flows exclusively through `audit.db`.

## Hard rules
- The LLM is the only decision-maker. No `if/else` in any file decides which action to apply to an instance.
- `cache-1` is protected via `tags["protected"] == "true"` check in `safety.py` only. Its name is never hardcoded anywhere.
- Zero hardcoded: instance names, IDs, region strings, API keys, or credentials in any Python file.
- The orchestrator loop must never crash. Every cycle is wrapped in `try/except` + `continue`.
- `.env` and `audit.db` are gitignored and never committed.
