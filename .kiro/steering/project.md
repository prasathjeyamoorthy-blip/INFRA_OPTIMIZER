---
inclusion: always
---

# Project — Dynamic Infrastructure Cost-Optimizer Agent

## What this is
An autonomous observe–decide–act loop that monitors live AWS EC2 instances, calls Claude once per cycle to decide what action to take, executes real boto3 calls, and exposes everything through a REST API that a Textual terminal dashboard polls every 3 seconds.

## Repo layout
```
.
├── orchestrator.py       # main loop — sequences every cycle
├── observer.py           # boto3: CloudWatch + EC2 reads
├── safety.py             # pre-LLM filter: protected + cooldown instances
├── llm.py                # Claude API call + prompt builder
├── validator.py          # post-LLM safety rails
├── executor.py           # boto3 tool dispatch
├── db.py                 # SQLAlchemy session + helper functions
├── models.py             # SQLAlchemy table definitions
├── api.py                # FastAPI route handlers
├── main.py               # FastAPI app + uvicorn entry point
├── audit.db              # SQLite (auto-created on first run)
└── .env                  # secrets — never commit
```

## Shared environment
All processes read from `.env`. Required keys:
```
AWS_ACCESS_KEY_ID
AWS_SECRET_ACCESS_KEY
AWS_DEFAULT_REGION=us-east-1
ANTHROPIC_API_KEY
DB_URL=sqlite:///./audit.db
POLL_INTERVAL=10
```

## Stack
Python 3.11+, boto3, anthropic, fastapi, uvicorn, sqlalchemy, pydantic, python-dotenv

## The four EC2 instances
| Name | Tag: protected |
|---|---|
| web-1 | false |
| web-2 | false |
| worker-1 | false |
| cache-1 | **true** |

All four are tagged `project=cost-optimizer-agent`. The agent only ever touches instances with this tag.

## Hard rules
- The LLM is the only decision-maker. No if/else logic in any file may decide which action to take on an instance.
- `cache-1` must never be acted on. This is enforced by the `protected=true` tag check in `safety.py`, not by hardcoding the name anywhere.
- Never commit `.env` or `audit.db`.
- The orchestrator loop must never crash. Wrap each cycle in try/except and continue.
