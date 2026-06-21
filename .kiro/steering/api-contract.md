---
inclusion: fileMatch
fileMatchPattern: "api.py|main.py|models.py|db.py"
---

# API Contract

The Textual UI is already built and polls these endpoints. Do not rename, reshape, or version them.

## Endpoints

### GET /api/instances
Returns current state of all four instances from the `instances` table.
```json
[
  {
    "id": "i-0d4e5f6",
    "name": "web-2",
    "type": "t3.micro",
    "status": "running",
    "tags": {"protected": "false"},
    "last_action": "start_instance",
    "last_action_reasoning": "Reversing prior stop — likely load-bearing despite low CPU."
  }
]
```

### GET /api/cycles?limit=50
Returns the N most recent cycle rows, sorted by `cycle` desc then `id` desc.
```json
[
  {
    "cycle": 14,
    "timestamp": "2026-06-21T10:04:20Z",
    "instance_id": "i-0d4e5f6",
    "instance_name": "web-2",
    "action": "start_instance",
    "reasoning": "Stopped this instance last cycle. Immediately after, web-1 latency spiked 6x. Reversing.",
    "validated": true,
    "aws_response": "success"
  }
]
```

### GET /api/cost
Computed from the `instances` table. Cost map: `t3.micro=$0.0104/hr`, `t3.small=$0.0208/hr`, `t3.medium=$0.0416/hr`. Savings = sum of stopped instance hourly costs.
```json
{ "estimated_hourly_savings": 0.0208, "running_instance_count": 3, "stopped_instance_count": 1 }
```

### GET /api/killswitch
```json
{ "active": false }
```
Read from the `kill_switch` table row with `id=1`. If row doesn't exist, return `false` and insert it.

### POST /api/killswitch
Body: `{ "active": true }` — update the row, return new state.

## CORS
Enable for all origins during development:
```python
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
```

## Database tables

### instances
`id` (PK, str), `name`, `instance_type`, `status`, `tags` (JSON str), `last_action`, `last_action_reasoning`, `updated_at`

### cycles
`id` (autoincrement PK), `cycle` (int), `timestamp`, `instance_id`, `instance_name`, `action`, `reasoning`, `validated` (bool), `aws_response`

### kill_switch
`id` (PK, default 1), `active` (bool), `toggled_at`
