---
inclusion: fileMatch
fileMatchPattern: "orchestrator.py|api.py|db.py"
---

# Kill Switch

## Behavior contract
- When active: orchestrator skips all actions every cycle, but still logs `do_nothing` for every instance with `reasoning="Kill switch active."`
- When inactive: orchestrator resumes normal operation on the very next cycle — no restart required
- Must take effect within one `POLL_INTERVAL` (≤10 seconds) of the UI button being pressed

## How it works
- `POST /api/killswitch` → writes to `kill_switch` table row `id=1`, returns new state immediately
- `GET /api/killswitch` → reads from same row; if row missing, return `{ "active": false }` and insert it
- Orchestrator calls `is_kill_switch_active()` as the **first thing** in every cycle — before OBSERVE, before the LLM call, before any boto3 call

## db.py helper
```python
def is_kill_switch_active() -> bool:
    with get_session() as session:
        row = session.query(KillSwitch).filter_by(id=1).first()
        return row.active if row else False
```
