"""
safety.py — Pre-LLM eligibility filter.

Pure functions only: no database or AWS calls.
"""

REAL_ACTIONS = {"stop_instance", "start_instance", "resize_instance", "tag_instance"}
COOLDOWN_CYCLES = 3


def filter_eligible_instances(state: dict, recent_history: list) -> list[str]:
    """
    Return a list of instance IDs eligible for LLM consideration this cycle.

    Exclusion rules (applied unconditionally, in order):
      1. Permanent exclusion: any instance whose tags["protected"] == "true".
         This check reads the tag value only — instance names are never compared.
      2. Cooldown exclusion: any instance that received a Real Action in the
         last COOLDOWN_CYCLES records of recent_history.

    Args:
        state:          The dict returned by observe_all(), containing an
                        "instances" key with a list of instance dicts.
        recent_history: The list of cycle-record dicts from get_recent_cycles().
                        Each record has at least "action" and "instance_id" keys.

    Returns:
        List of instance ID strings that passed both exclusion rules.
    """
    # --- Rule 2 pre-computation: collect IDs on cooldown ---
    # Only examine the most recent COOLDOWN_CYCLES records.
    cooldown_window = recent_history[:COOLDOWN_CYCLES]
    on_cooldown: set[str] = {
        record["instance_id"]
        for record in cooldown_window
        if record.get("action") in REAL_ACTIONS
    }

    eligible: list[str] = []

    for instance in state.get("instances", []):
        instance_id: str = instance["id"]

        # Rule 1 — permanent exclusion via protected tag
        if instance.get("tags", {}).get("protected") == "true":
            continue

        # Rule 2 — cooldown exclusion
        if instance_id in on_cooldown:
            continue

        eligible.append(instance_id)

    return eligible
