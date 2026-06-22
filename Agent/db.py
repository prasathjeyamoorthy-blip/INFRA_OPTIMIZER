"""
db.py — SQLAlchemy session factory and database helper functions.

Exposes five helper functions used by the Orchestrator and API Server:
  - init_db()                  → create all tables on first run
  - upsert_instance_snapshot() → insert or update an Instance row
  - write_cycle_records()      → bulk-insert Cycle rows
  - is_kill_switch_active()    → read the most recent KillSwitch row
  - get_recent_cycles()        → return recent Cycle rows as dicts

DB_URL is read from the environment (loaded from .env via python-dotenv).
Raises KeyError if DB_URL is not set.
"""

import json
import os
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from models import Base, Cycle, Instance, KillSwitch

# ---------------------------------------------------------------------------
# Load .env and resolve DB_URL — raises KeyError if missing
# ---------------------------------------------------------------------------
load_dotenv(dotenv_path=Path(__file__).resolve().parent / ".env")

DB_URL: str = os.environ["DB_URL"]

# ---------------------------------------------------------------------------
# Engine and session factory
# ---------------------------------------------------------------------------
engine = create_engine(DB_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine)


# ---------------------------------------------------------------------------
# Public helper functions
# ---------------------------------------------------------------------------

def init_db() -> None:
    """Create all tables if they do not exist.

    Called once at startup by both the Orchestrator and the API Server.
    On the very first run this creates ``audit.db`` and all three tables.
    """
    Base.metadata.create_all(engine)


def upsert_instance_snapshot(instance_data: dict) -> None:
    """Insert or update a row in the ``instances`` table.

    The row is keyed on ``instance_data["id"]``.  If a row with that primary
    key already exists it is overwritten; otherwise a new row is inserted.

    ``tags`` is stored as a JSON string regardless of whether the caller
    passes a dict or an already-serialised string.

    Args:
        instance_data: Dict with at minimum an ``"id"`` key matching the EC2
            instance-id.  Recognised keys: ``id``, ``name``,
            ``instance_type``, ``status``, ``tags``, ``last_action``,
            ``last_action_reasoning``.
    """
    tags = instance_data.get("tags", {})
    if not isinstance(tags, str):
        tags = json.dumps(tags)

    instance = Instance(
        id=instance_data["id"],
        name=instance_data.get("name"),
        instance_type=instance_data.get("instance_type"),
        status=instance_data.get("status"),
        tags=tags,
        last_action=instance_data.get("last_action"),
        last_action_reasoning=instance_data.get("last_action_reasoning"),
        pending_resize=instance_data.get("pending_resize"),
    )

    session = SessionLocal()
    try:
        session.merge(instance)
        session.commit()
    finally:
        session.close()


def write_cycle_records(records: list[dict]) -> None:
    """Bulk-insert rows into the ``cycles`` table.

    Each dict in *records* is mapped to a :class:`~models.Cycle` ORM object.
    Unrecognised keys in the dict are silently ignored.

    Args:
        records: List of dicts.  Recognised keys: ``cycle``, ``instance_id``,
            ``instance_name``, ``action``, ``reasoning``, ``validated``,
            ``aws_response``.
    """
    if not records:
        return

    session = SessionLocal()
    try:
        for record in records:
            ts = record.get("timestamp")
            if isinstance(ts, str):
                from datetime import datetime
                try:
                    ts = datetime.fromisoformat(ts)
                except Exception:
                    ts = None
            cycle_row = Cycle(
                cycle=record.get("cycle"),
                timestamp=ts,
                instance_id=record.get("instance_id"),
                instance_name=record.get("instance_name"),
                action=record.get("action"),
                reasoning=record.get("reasoning"),
                validated=record.get("validated"),
                aws_response=record.get("aws_response"),
            )
            session.add(cycle_row)
        session.commit()
    finally:
        session.close()


def is_kill_switch_active() -> bool:
    """Return the ``active`` field of the most recent kill-switch row.

    Queries the ``kill_switch`` table ordered by ``id`` DESC and returns the
    ``active`` boolean of the first row.  Returns ``False`` when the table is
    empty (i.e. no toggle has ever been recorded).

    Returns:
        ``True`` if the kill switch is currently active, ``False`` otherwise.
    """
    session = SessionLocal()
    try:
        row = (
            session.query(KillSwitch)
            .order_by(KillSwitch.id.desc())
            .first()
        )
        if row is None:
            return False
        return bool(row.active)
    finally:
        session.close()


def sync_instances_from_aws() -> int:
    """
    Fetch live EC2 instance data from AWS and reconcile the `instances` table.

    On every startup this ensures the DB reflects the actual state of AWS:
    - Only non-terminated instances are upserted
    - Terminated and shutting-down instances are deleted from the DB
    - Instances in the DB that no longer exist in AWS are deleted

    Returns:
        The number of live (non-terminated) instances found in AWS.
    """
    import os
    import boto3
    from botocore.config import Config

    _EXCLUDED_STATES = {"terminated", "shutting-down"}

    cfg = Config(connect_timeout=10, read_timeout=20, retries={"max_attempts": 2})
    ec2 = boto3.client(
        "ec2",
        aws_access_key_id=os.environ["AWS_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["AWS_SECRET_ACCESS_KEY"],
        region_name=os.environ["AWS_DEFAULT_REGION"],
        config=cfg,
    )

    # Fetch all instances tagged for this project (including terminated — so we
    # can explicitly delete them from the DB)
    resp = ec2.describe_instances(
        Filters=[{"Name": "tag:project", "Values": ["cost-optimizer-agent"]}]
    )

    live_ids: set[str] = set()        # non-terminated instances
    terminated_ids: set[str] = set()  # terminated / shutting-down

    for reservation in resp.get("Reservations", []):
        for raw in reservation.get("Instances", []):
            instance_id = raw["InstanceId"]
            state = raw.get("State", {}).get("Name", "").lower()

            if state in _EXCLUDED_STATES:
                terminated_ids.add(instance_id)
                continue

            raw_tags = raw.get("Tags") or []
            tags = {t["Key"]: t["Value"] for t in raw_tags}
            instance_data = {
                "id":            instance_id,
                "name":          tags.get("Name", ""),
                "instance_type": raw.get("InstanceType", ""),
                "status":        state,
                "tags":          tags,
            }
            upsert_instance_snapshot(instance_data)
            live_ids.add(instance_id)

    # Delete terminated instances and stale rows from DB
    session = SessionLocal()
    try:
        all_db_ids = {row.id for row in session.query(Instance.id).all()}
        ids_to_delete = (all_db_ids - live_ids) | terminated_ids
        if ids_to_delete:
            session.query(Instance).filter(Instance.id.in_(ids_to_delete)).delete(
                synchronize_session=False
            )
            session.commit()
    finally:
        session.close()

    return len(live_ids)


def get_recent_cycles(limit: int = 50) -> list[dict]:
    """Return the most recent *limit* rows from the ``cycles`` table.

    Rows are ordered by ``timestamp`` DESC so index 0 is always the most
    recent record.  Each row is returned as a plain dict so callers do not
    need to depend on the SQLAlchemy ORM.

    Args:
        limit: Maximum number of rows to return (default 50).

    Returns:
        List of dicts with keys: ``id``, ``cycle``, ``timestamp``,
        ``instance_id``, ``instance_name``, ``action``, ``reasoning``,
        ``validated``, ``aws_response``.
    """
    session = SessionLocal()
    try:
        rows = (
            session.query(Cycle)
            .order_by(Cycle.timestamp.desc())
            .limit(limit)
            .all()
        )
        return [
            {
                "id": row.id,
                "cycle": row.cycle,
                "timestamp": row.timestamp.isoformat() if row.timestamp else None,
                "instance_id": row.instance_id,
                "instance_name": row.instance_name,
                "action": row.action,
                "reasoning": row.reasoning,
                "validated": row.validated,
                "aws_response": row.aws_response,
            }
            for row in rows
        ]
    finally:
        session.close()


def set_pending_resize(instance_id: str, target_type: str | None) -> None:
    """Mark an instance as needing a resize to target_type after it stops.
    Pass target_type=None to clear the pending resize flag.
    """
    session = SessionLocal()
    try:
        row = session.query(Instance).filter(Instance.id == instance_id).first()
        if row:
            row.pending_resize = target_type
            session.commit()
    finally:
        session.close()


def get_pending_resizes() -> dict[str, str]:
    """Return {instance_id: target_type} for all instances with a pending resize."""
    session = SessionLocal()
    try:
        rows = session.query(Instance).filter(Instance.pending_resize.isnot(None)).all()
        return {row.id: row.pending_resize for row in rows}
    finally:
        session.close()
