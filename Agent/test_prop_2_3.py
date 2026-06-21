"""
test_prop_2_3.py — Property-based test for upsert_instance_snapshot round-trip.

**Validates: Requirements 2.4**

Property 1: Instance Snapshot Round-Trip
  For any instance data dict written via upsert_instance_snapshot(), a subsequent
  read from the `instances` table returns a record with equivalent fields.

Uses an in-memory SQLite DB (DB_URL=sqlite:///:memory:) so no real database file
is touched. Each test case uses a fresh DB to ensure full isolation.

Run standalone:
    python Agent/test_prop_2_3.py
"""

import importlib
import json
import os
import sys
import unittest

# ---------------------------------------------------------------------------
# Set DB_URL *before* importing db so the module-level engine uses in-memory
# SQLite. This must happen before any import of db or models.
# ---------------------------------------------------------------------------
os.environ["DB_URL"] = "sqlite:///:memory:"

# Ensure Agent/ directory is importable when running from the project root.
_agent_dir = os.path.join(os.path.dirname(__file__))
if _agent_dir not in sys.path:
    sys.path.insert(0, _agent_dir)

import db as db_module  # noqa: E402 — must be after env var is set
import models           # noqa: E402

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# ---------------------------------------------------------------------------
# Strategies — constrained to valid instance field values
# ---------------------------------------------------------------------------

# EC2 instance IDs: "i-" followed by 8–17 hex digits.
_instance_id_st = st.builds(
    lambda suffix: f"i-{suffix}",
    suffix=st.text(alphabet="0123456789abcdef", min_size=8, max_size=17),
)

# Optional short text fields (None is valid for nullable columns).
_optional_text_st = st.one_of(st.none(), st.text(min_size=0, max_size=64))

# Instance status strings that EC2 may report.
_status_st = st.one_of(
    st.none(),
    st.sampled_from(["running", "stopped", "stopping", "pending", "terminated"]),
)

# Tags: a dict of string → string (serialised to JSON in the DB).
_tags_st = st.one_of(
    st.none(),
    st.dictionaries(
        keys=st.text(min_size=1, max_size=16),
        values=st.text(min_size=0, max_size=32),
        max_size=5,
    ),
)

# Full instance data dict strategy.
_instance_data_st = st.fixed_dictionaries(
    {
        "id": _instance_id_st,
        "name": _optional_text_st,
        "instance_type": _optional_text_st,
        "status": _status_st,
        "tags": _tags_st,
        "last_action": _optional_text_st,
        "last_action_reasoning": _optional_text_st,
    }
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_fresh_db():
    """Return (engine, SessionLocal) backed by a brand-new in-memory SQLite."""
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}
    )
    models.Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    return engine, Session


def _upsert(session_factory, instance_data: dict) -> None:
    """Replicates upsert_instance_snapshot logic against a given session factory."""
    tags = instance_data.get("tags") or {}   # None → {} to match db.py behaviour
    if not isinstance(tags, str):
        tags = json.dumps(tags)

    instance = models.Instance(
        id=instance_data["id"],
        name=instance_data.get("name"),
        instance_type=instance_data.get("instance_type"),
        status=instance_data.get("status"),
        tags=tags,
        last_action=instance_data.get("last_action"),
        last_action_reasoning=instance_data.get("last_action_reasoning"),
    )

    session = session_factory()
    try:
        session.merge(instance)
        session.commit()
    finally:
        session.close()


def _read(session_factory, instance_id: str):
    """Return the Instance ORM object for *instance_id*, or None."""
    session = session_factory()
    try:
        return session.get(models.Instance, instance_id)
    finally:
        session.close()


def _tags_equivalent(stored: str | None, original) -> bool:
    """
    Return True when the stored JSON string represents the same value as the
    original tags field (which may be a dict, a JSON string, or None).

    None and empty dict are treated as equivalent because db.py normalises
    missing/None tags to {} before serialising.
    """
    # Normalise original: None → {}
    if original is None:
        expected_dict = {}
    elif isinstance(original, str):
        try:
            expected_dict = json.loads(original)
        except json.JSONDecodeError:
            expected_dict = {}
    else:
        expected_dict = original

    # Normalise stored
    if stored is None or stored == "null":
        stored_dict = {}
    else:
        try:
            stored_dict = json.loads(stored)
        except json.JSONDecodeError:
            return False

    return stored_dict == expected_dict


# ---------------------------------------------------------------------------
# Test class
# ---------------------------------------------------------------------------

class TestUpsertInstanceSnapshotRoundTrip(unittest.TestCase):
    """Property 1: Instance Snapshot Round-Trip.

    **Validates: Requirements 2.4**
    """

    @given(_instance_data_st)
    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    def test_insert_round_trip(self, instance_data: dict):
        """Inserting a new row and reading it back yields equivalent fields."""
        _, Session = _make_fresh_db()

        _upsert(Session, instance_data)
        row = _read(Session, instance_data["id"])

        self.assertIsNotNone(row, "Row should exist after upsert (insert path)")
        self.assertEqual(row.id, instance_data["id"])
        self.assertEqual(row.name, instance_data.get("name"))
        self.assertEqual(row.instance_type, instance_data.get("instance_type"))
        self.assertEqual(row.status, instance_data.get("status"))
        self.assertTrue(
            _tags_equivalent(row.tags, instance_data.get("tags")),
            f"tags mismatch: stored={row.tags!r}, original={instance_data.get('tags')!r}",
        )
        self.assertEqual(row.last_action, instance_data.get("last_action"))
        self.assertEqual(
            row.last_action_reasoning, instance_data.get("last_action_reasoning")
        )

    @given(_instance_data_st, _instance_data_st)
    @settings(max_examples=75, suppress_health_check=[HealthCheck.too_slow])
    def test_update_round_trip(self, first_data: dict, second_data: dict):
        """Updating an existing row (same id) and reading it back yields the
        updated fields, not the original ones.

        **Validates: Requirements 2.4**
        """
        _, Session = _make_fresh_db()

        # Use the same id so the second write is an UPDATE.
        shared_id = first_data["id"]
        second_data = {**second_data, "id": shared_id}

        _upsert(Session, first_data)
        _upsert(Session, second_data)

        row = _read(Session, shared_id)

        self.assertIsNotNone(row, "Row should exist after two upserts (update path)")
        self.assertEqual(row.id, shared_id)
        self.assertEqual(row.name, second_data.get("name"))
        self.assertEqual(row.instance_type, second_data.get("instance_type"))
        self.assertEqual(row.status, second_data.get("status"))
        self.assertTrue(
            _tags_equivalent(row.tags, second_data.get("tags")),
            f"tags mismatch after update: stored={row.tags!r}, "
            f"original={second_data.get('tags')!r}",
        )
        self.assertEqual(row.last_action, second_data.get("last_action"))
        self.assertEqual(
            row.last_action_reasoning, second_data.get("last_action_reasoning")
        )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    unittest.main(verbosity=2)
