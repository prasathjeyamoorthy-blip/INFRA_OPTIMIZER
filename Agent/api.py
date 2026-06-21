"""
api.py — Pydantic schemas and FastAPI route handlers.

Exposes five REST endpoints under the /api prefix:
  GET  /api/instances    → current instance snapshots
  GET  /api/cycles       → recent cycle records (limit: int = 50)
  GET  /api/cost         → cost summary derived from live instance data
  GET  /api/killswitch   → current kill-switch state
  POST /api/killswitch   → toggle the kill switch (body: {"active": bool})

All database access uses SessionLocal from db.py.
The tags column in Instance is a JSON string; it is parsed before returning.
"""

import json
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from db import SessionLocal, get_recent_cycles
from models import Instance, KillSwitch

# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------

class InstanceSchema(BaseModel):
    id: str
    name: str
    instance_type: str
    status: str
    tags: dict
    last_action: str | None
    last_action_reasoning: str | None
    updated_at: datetime

    model_config = {"from_attributes": True}


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

    model_config = {"from_attributes": True}


class CostSummarySchema(BaseModel):
    total_instances: int
    running_count: int
    stopped_count: int
    estimated_hourly_usd: float


class KillSwitchSchema(BaseModel):
    active: bool
    toggled_at: datetime | None


# ---------------------------------------------------------------------------
# Simple instance-type pricing lookup (per-hour USD, running instances only).
# Add more types here as needed — never hardcode instance-specific costs
# outside this table.
# ---------------------------------------------------------------------------
_INSTANCE_PRICING_USD_PER_HOUR: dict[str, float] = {
    "t2.nano":    0.0058,
    "t2.micro":   0.0116,
    "t2.small":   0.0230,
    "t2.medium":  0.0464,
    "t2.large":   0.0928,
    "t3.nano":    0.0052,
    "t3.micro":   0.0104,
    "t3.small":   0.0208,
    "t3.medium":  0.0416,
    "t3.large":   0.0832,
    "t3.xlarge":  0.1664,
    "t3.2xlarge": 0.3328,
    "m5.large":   0.0960,
    "m5.xlarge":  0.1920,
    "c5.large":   0.0850,
    "c5.xlarge":  0.1700,
    "r5.large":   0.1260,
    "r5.xlarge":  0.2520,
}

_DEFAULT_HOURLY_RATE = 0.05  # fallback for unknown instance types


# ---------------------------------------------------------------------------
# DB session dependency
# ---------------------------------------------------------------------------

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

router = APIRouter(prefix="/api")


# ---------------------------------------------------------------------------
# GET /api/instances
# ---------------------------------------------------------------------------

@router.get("/instances", response_model=list[InstanceSchema])
def list_instances(db: Session = Depends(get_db)):
    """Return all instance snapshots from the instances table."""
    rows = db.query(Instance).all()
    result = []
    for row in rows:
        # Parse tags from JSON string to dict
        tags = row.tags
        if isinstance(tags, str):
            try:
                tags = json.loads(tags)
            except (json.JSONDecodeError, TypeError):
                tags = {}
        elif tags is None:
            tags = {}

        result.append(
            InstanceSchema(
                id=row.id,
                name=row.name or "",
                instance_type=row.instance_type or "",
                status=row.status or "",
                tags=tags,
                last_action=row.last_action,
                last_action_reasoning=row.last_action_reasoning,
                updated_at=row.updated_at or datetime.utcnow(),
            )
        )
    return result


# ---------------------------------------------------------------------------
# GET /api/cycles
# ---------------------------------------------------------------------------

@router.get("/cycles", response_model=list[CycleSchema])
def list_cycles(limit: int = 50):
    """Return the most recent `limit` cycle records."""
    records = get_recent_cycles(limit=limit)
    result = []
    for r in records:
        result.append(
            CycleSchema(
                id=r["id"],
                cycle=r["cycle"] if r["cycle"] is not None else 0,
                timestamp=r["timestamp"] or datetime.utcnow(),
                instance_id=r["instance_id"] or "",
                instance_name=r["instance_name"] or "",
                action=r["action"] or "",
                reasoning=r["reasoning"] or "",
                validated=bool(r["validated"]),
                aws_response=r["aws_response"] or "",
            )
        )
    return result


# ---------------------------------------------------------------------------
# GET /api/cost
# ---------------------------------------------------------------------------

@router.get("/cost", response_model=CostSummarySchema)
def get_cost_summary(db: Session = Depends(get_db)):
    """Derive cost summary from live instance data in the DB."""
    rows = db.query(Instance).all()

    total_instances = len(rows)
    running_count = 0
    stopped_count = 0
    estimated_hourly_usd = 0.0

    for row in rows:
        status = (row.status or "").lower()
        if status == "running":
            running_count += 1
            hourly_rate = _INSTANCE_PRICING_USD_PER_HOUR.get(
                row.instance_type or "", _DEFAULT_HOURLY_RATE
            )
            estimated_hourly_usd += hourly_rate
        elif status in ("stopped", "stopping"):
            stopped_count += 1

    return CostSummarySchema(
        total_instances=total_instances,
        running_count=running_count,
        stopped_count=stopped_count,
        estimated_hourly_usd=round(estimated_hourly_usd, 6),
    )


# ---------------------------------------------------------------------------
# GET /api/killswitch
# ---------------------------------------------------------------------------

@router.get("/killswitch", response_model=KillSwitchSchema)
def get_killswitch(db: Session = Depends(get_db)):
    """Return the current kill-switch state (most recent row)."""
    row = (
        db.query(KillSwitch)
        .order_by(KillSwitch.id.desc())
        .first()
    )
    if row is None:
        return KillSwitchSchema(active=False, toggled_at=None)
    return KillSwitchSchema(active=bool(row.active), toggled_at=row.toggled_at)


# ---------------------------------------------------------------------------
# POST /api/killswitch
# ---------------------------------------------------------------------------

class KillSwitchRequest(BaseModel):
    active: bool


@router.post("/killswitch", response_model=KillSwitchSchema)
def set_killswitch(body: KillSwitchRequest, db: Session = Depends(get_db)):
    """Insert a new kill-switch row and return its state."""
    new_row = KillSwitch(active=body.active)
    db.add(new_row)
    db.commit()
    db.refresh(new_row)
    return KillSwitchSchema(active=bool(new_row.active), toggled_at=new_row.toggled_at)
