"""
validator.py — Post-LLM safety rails.

Filters and caps the list of actions proposed by the LLM before any
boto3 call is made. This is a pure function module: no database or AWS
calls are made here.
"""

ALLOWED_TOOLS = {
    "stop_instance",
    "start_instance",
    "resize_instance",
    "tag_instance",
    "do_nothing",
    "alert_human",
}

REAL_ACTIONS = {
    "stop_instance",
    "start_instance",
    "resize_instance",
    "tag_instance",
}

MAX_REAL_ACTIONS_PER_CYCLE = 2


def validate_actions(
    llm_response: list[dict],
    state: dict,
    eligible_ids: list[str],
) -> list[dict]:
    """
    Filter the LLM-proposed action list to only safe, valid actions.

    Rules applied in order:
      1. Drop any action whose "tool" is not in ALLOWED_TOOLS.
      2. Drop any action whose "instance_id" is not in eligible_ids.
      3. After rules 1-2, cap Real Actions at MAX_REAL_ACTIONS_PER_CYCLE (2);
         keep the first 2 real actions and drop the rest.
         Non-real actions (do_nothing, alert_human) are not counted against
         the cap and are retained without restriction.

    Args:
        llm_response: Raw list of action dicts from the LLM.
        state:        Current observed state dict (not used in filtering logic,
                      kept for signature compatibility with the orchestrator).
        eligible_ids: List of instance IDs that passed the pre-LLM safety filter.

    Returns:
        Filtered and capped list of action dicts. Invalid actions are silently
        dropped; no exceptions are raised.
    """
    eligible_set = set(eligible_ids)

    # Rule 1 + Rule 2: drop unknown tools and ineligible instance IDs
    filtered = [
        action
        for action in llm_response
        if action.get("tool") in ALLOWED_TOOLS
        and action.get("instance_id") in eligible_set
    ]

    # Rule 3: cap Real Actions at MAX_REAL_ACTIONS_PER_CYCLE; non-real actions
    # pass through unconditionally.
    result: list[dict] = []
    real_action_count = 0

    for action in filtered:
        if action["tool"] in REAL_ACTIONS:
            if real_action_count < MAX_REAL_ACTIONS_PER_CYCLE:
                result.append(action)
                real_action_count += 1
            # else: silently drop — cap exceeded
        else:
            # do_nothing / alert_human — not counted against the cap
            result.append(action)

    return result
