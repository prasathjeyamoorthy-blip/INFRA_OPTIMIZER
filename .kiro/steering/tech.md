# Tech Stack

## Language
Python 3.11+

## Core libraries (pinned in `Agent/requirements.txt`)
| Library | Purpose |
|---|---|
| `boto3==1.34.69` | AWS SDK — EC2 + CloudWatch calls |
| `anthropic==0.25.1` | Claude API client |
| `fastapi==0.110.1` | REST API framework |
| `uvicorn==0.29.0` | ASGI server for FastAPI |
| `sqlalchemy==2.0.29` | ORM + SQLite session management |
| `pydantic==2.6.4` | Request/response schemas |
| `python-dotenv==1.0.1` | `.env` loading |

## Database
SQLite (`audit.db`) accessed via SQLAlchemy. Use `connect_args={"check_same_thread": False}` and a `SessionLocal` factory for safe concurrent access.

## LLM
Claude `claude-sonnet-4-6` via the Anthropic Python SDK. One API call per orchestrator cycle. Response must be a raw JSON array — strip markdown fences before parsing.

## Common commands

```bash
# Install dependencies
pip install -r Agent/requirements.txt

# Run API server
python Agent/main.py

# Run orchestrator loop (separate terminal)
python Agent/orchestrator.py

# Run synthetic metric publisher (separate terminal)
python Agent/publisher.py

# Provision AWS infrastructure (run once)
python Agent/setup_aws.py

# Run smoke tests (no live AWS required)
DB_URL=sqlite:///:memory: python Agent/test_smoke.py
```

## Environment variables (all read from `.env` via python-dotenv)
```
AWS_ACCESS_KEY_ID=
AWS_SECRET_ACCESS_KEY=
AWS_DEFAULT_REGION=us-east-1
ANTHROPIC_API_KEY=
DB_URL=sqlite:///./audit.db
POLL_INTERVAL=10
API_HOST=0.0.0.0
API_PORT=8000
```

Never hardcode any of these values in source code.
