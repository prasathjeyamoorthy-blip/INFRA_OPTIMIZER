"""
orchestrator.py — Main observe–decide–act loop.

Sequences every cycle in exactly 10 steps:
  1.  Read kill switch state
  2.  Observe all EC2 instances and their CloudWatch metrics
  3.  Persist instance snapshots to the database
  4.  Load recent cycle history from the database
  5.  Filter eligible instances (pre-LLM safety check)
  6.  Call the LLM to get proposed actions          ┐ skipped when
  7.  Validate proposed actions                     │ kill switch is
  8.  Execute each validated action via TOOL_DISPATCH┘ active
  9.  Persist cycle records to the database
  10. Sleep for POLL_INTERVAL seconds (always executed)

The entire cycle body (steps 1–9) is wrapped in a single try/except
Exception so that no individual step failure can ever terminate the loop.
Step 10 (sleep) is outside the try block to ensure the interval is always
respected even after a failed cycle.

Zero hardcoded values: no instance names, IDs, region strings, credentials,
or metric values appear anywhere in this file.
"""

import json
import logging
import os
import time
from pathlib import Path

from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Load .env before importing any modules that read environment variables
# ---------------------------------------------------------------------------

load_dotenv(dotenv_path=Path(__file__).resolve().parent / ".env")

# ---------------------------------------------------------------------------
# Module imports from sibling Agent modules
# ---------------------------------------------------------------------------

from db import (
    get_recent_cycles,
    init_db,
    is_kill_switch_active,
    upsert_instance_snapshot,
    write_cycle_records,
)
from executor import TOOL_DISPATCH
from llm import call_llm
from observer import observe_all
from safety import filter_eligible_instances
from validator import validate_actions

# ---------------------------------------------------------------------------
# Configuration — loaded from environment; zero hardcoded defaults for
# business logic, only the poll interval has a safe fallback of 10 seconds.
# ---------------------------------------------------------------------------

poll_interval: int = int(os.getenv("POLL_INTERVAL", "10"))

# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

def _name_for(state: dict, instance_id: str) -> str:
    """Look up the instance name from *state* by instance ID.

    Iterates over ``state["instances"]`` and returns the ``name`` field of
    the first entry whose ``id`` matches *instance_id*.  Returns an empty
    string if no match is found.

    This helper contains zero hardcoded names — all name resolution is
    performed dynamically against the live observed state.

    Args:
        state:       The dict returned by :func:`observe_all`.
        instance_id: The EC2 instance ID to look up.

    Returns:
        The instance name string, or ``""`` if not found.
    """
    for inst in state.get("instances", []):
        if inst.get("id") == instance_id:
            return inst.get("name", "")
    return ""


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def run() -> None:
    """Start the orchestrator loop.

    Initialises the database, then enters a ``while True`` loop that runs
    indefinitely.  Each iteration represents one complete observe–decide–act
    cycle.  The loop is designed to never exit: any exception raised during
    steps 1–9 is caught, logged, and the loop continues with the next cycle.
    """
    init_db()
    logger.info("Database initialised.  Starting orchestrator loop.")

    cycle_num: int = 0

    while True:
        cycle_num += 1
        records: list[dict] = []

        try:
            # ------------------------------------------------------------------
            # Step 1 — Check kill switch state
            # ------------------------------------------------------------------
            kill_active: bool = is_kill_switch_active()
            if kill_active:
                logger.info("Cycle %d: kill switch is ACTIVE — LLM and executor will be skipped.", cycle_num)

            # ------------------------------------------------------------------
            # Step 2 — Observe current AWS state
            # ------------------------------------------------------------------
            state: dict = observe_all()
            logger.info(
                "Cycle %d: observed %d instance(s).",
                cycle_num,
                len(state.get("instances", [])),
            )

            # ------------------------------------------------------------------
            # Step 3 — Persist instance snapshots
            # ------------------------------------------------------------------
            for inst in state.get("instances", []):
                upsert_instance_snapshot(inst)

            # ------------------------------------------------------------------
            # Step 4 — Load recent cycle history
            # ------------------------------------------------------------------
            history: list[dict] = get_recent_cycles(limit=50)

            # ------------------------------------------------------------------
            # Step 5 — Filter eligible instances (pre-LLM safety check)
            # ------------------------------------------------------------------
            eligible_ids: list[str] = filter_eligible_instances(state, history)
            logger.info(
                "Cycle %d: %d eligible instance(s): %s",
                cycle_num,
                len(eligible_ids),
                eligible_ids,
            )

            # ------------------------------------------------------------------
            # Steps 6–8 — LLM decision + validation + execution
            # Skipped entirely when the kill switch is active.
            # ------------------------------------------------------------------
            if not kill_active:
                # Step 6 — Ask the LLM to decide what actions to take
                llm_actions: list[dict] = call_llm(state, eligible_ids, history)
                logger.info(
                    "Cycle %d: LLM proposed %d action(s).",
                    cycle_num,
                    len(llm_actions),
                )

                # Step 7 — Validate LLM proposals against safety rails
                validated: list[dict] = validate_actions(llm_actions, state, eligible_ids)
                logger.info(
                    "Cycle %d: %d action(s) passed validation.",
                    cycle_num,
                    len(validated),
                )

                # Step 8 — Dispatch each validated action via TOOL_DISPATCH
                for action in validated:
                    tool_fn = TOOL_DISPATCH[action["tool"]]
                    aws_resp: dict = tool_fn(**action)

                    records.append(
                        {
                            "cycle":         cycle_num,
                            "instance_id":   action.get("instance_id"),
                            "instance_name": _name_for(state, action.get("instance_id", "")),
                            "action":        action["tool"],
                            "reasoning":     action.get("reasoning", ""),
                            "validated":     True,
                            "aws_response":  json.dumps(aws_resp),
                        }
                    )

            # ------------------------------------------------------------------
            # Step 9 — Persist cycle records (only if any actions were taken)
            # ------------------------------------------------------------------
            if records:
                write_cycle_records(records)
                logger.info(
                    "Cycle %d: persisted %d cycle record(s).",
                    cycle_num,
                    len(records),
                )

        except Exception:
            # Log the full traceback but keep the loop alive; the next cycle
            # will begin after the sleep below.
            logger.exception("Cycle %d failed — continuing to next cycle.", cycle_num)

        # ----------------------------------------------------------------------
        # Step 10 — Sleep between cycles (outside the try block so it always runs)
        # ----------------------------------------------------------------------
        time.sleep(poll_interval)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    run()
