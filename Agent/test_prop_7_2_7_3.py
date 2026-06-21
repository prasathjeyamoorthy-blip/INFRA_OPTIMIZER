"""
test_prop_7_2_7_3.py — Property-based tests for validator.py.

Property 8: Validator Rejects Unknown and Ineligible Actions
  For any list of LLM-proposed actions where some have tool names outside
  ALLOWED_TOOLS or target instance IDs outside eligible_ids, validate_actions()
  returns only actions with valid tool names AND eligible target IDs.

  **Validates: Requirements 8.2, 8.3**

Property 9: Real Action Cap Enforcement
  For any list of validated actions containing more than 2 Real Actions,
  validate_actions() returns at most 2 Real Actions; non-real actions
  (do_nothing, alert_human) beyond that count are retained unrestricted.

  **Validates: Requirements 8.4**

Run with pytest:
    pytest Agent/test_prop_7_2_7_3.py -v

Run standalone:
    python Agent/test_prop_7_2_7_3.py
"""

import os
import sys
import unittest

# Ensure Agent/ directory is importable when running from the project root.
_agent_dir = os.path.dirname(__file__)
if _agent_dir not in sys.path:
    sys.path.insert(0, _agent_dir)

# Set a dummy DB_URL so importing db.py (indirectly via orchestrator, etc.)
# does not fail. validator.py itself has no DB dependency, but this guard is
# here in case pytest collects other modules.
os.environ.setdefault("DB_URL", "sqlite:///:memory:")

from validator import (  # noqa: E402
    ALLOWED_TOOLS,
    REAL_ACTIONS,
    MAX_REAL_ACTIONS_PER_CYCLE,
    validate_actions,
)

from hypothesis import HealthCheck, given, settings  # noqa: E402
from hypothesis import strategies as st              # noqa: E402

# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

# EC2 instance IDs: "i-" followed by 8–17 hex digits.
_instance_id_st = st.builds(
    lambda suffix: f"i-{suffix}",
    suffix=st.text(alphabet="0123456789abcdef", min_size=8, max_size=17),
)

# Valid tool names (the six allowed tools).
_valid_tool_st = st.sampled_from(sorted(ALLOWED_TOOLS))

# Invalid tool names: text that is definitely not in ALLOWED_TOOLS.
_invalid_tool_st = st.text(min_size=1, max_size=32).filter(
    lambda t: t not in ALLOWED_TOOLS
)

# Real action tool names.
_real_tool_st = st.sampled_from(sorted(REAL_ACTIONS))

# Non-real action tool names (do_nothing / alert_human).
_non_real_tool_st = st.sampled_from(
    sorted(ALLOWED_TOOLS - REAL_ACTIONS)
)

# A short reasoning string.
_reasoning_st = st.text(min_size=0, max_size=64)


def _action(tool_st, instance_id_st):
    """Build an action dict strategy from given tool and instance_id strategies."""
    return st.fixed_dictionaries(
        {
            "tool": tool_st,
            "instance_id": instance_id_st,
            "reasoning": _reasoning_st,
        }
    )


# ---------------------------------------------------------------------------
# Property 8: Validator Rejects Unknown and Ineligible Actions
# ---------------------------------------------------------------------------


class TestValidatorRejectsUnknownAndIneligible(unittest.TestCase):
    """Property 8 — Validator Rejects Unknown and Ineligible Actions.

    **Validates: Requirements 8.2, 8.3**
    """

    @given(
        # A non-empty list of instance IDs that ARE eligible.
        eligible_ids=st.lists(_instance_id_st, min_size=1, max_size=8, unique=True),
        # Actions with valid tools targeting eligible IDs.
        valid_actions=st.lists(
            st.fixed_dictionaries(
                {
                    "tool": _valid_tool_st,
                    # instance_id will be drawn from eligible_ids via flatmap
                    "reasoning": _reasoning_st,
                }
            ),
            min_size=0,
            max_size=10,
        ),
        # Actions with invalid (unknown) tool names.
        invalid_tool_actions=st.lists(
            st.fixed_dictionaries(
                {
                    "tool": _invalid_tool_st,
                    "instance_id": _instance_id_st,
                    "reasoning": _reasoning_st,
                }
            ),
            min_size=0,
            max_size=5,
        ),
        # Actions with valid tools but instance IDs NOT in eligible_ids.
        ineligible_id_actions=st.lists(
            st.fixed_dictionaries(
                {
                    "tool": _valid_tool_st,
                    "instance_id": st.text(min_size=1, max_size=20).filter(
                        # These must not accidentally be valid EC2 IDs in eligible set.
                        # We use "x-" prefix to keep them distinct from "i-..." IDs.
                        lambda s: not s.startswith("i-")
                    ),
                    "reasoning": _reasoning_st,
                }
            ),
            min_size=0,
            max_size=5,
        ),
    )
    @settings(max_examples=150, suppress_health_check=[HealthCheck.too_slow])
    def test_only_valid_tool_and_eligible_id_pass(
        self,
        eligible_ids,
        valid_actions,
        invalid_tool_actions,
        ineligible_id_actions,
    ):
        """
        After mixing valid, unknown-tool, and ineligible-id actions, every
        action in the output has a tool in ALLOWED_TOOLS AND an instance_id
        in eligible_ids.

        **Validates: Requirements 8.2, 8.3**
        """
        # Assign instance_id from eligible_ids to valid_actions.
        # (hypothesis can't easily cross-reference lists, so we do it here.)
        valid_actions_with_ids = [
            {**a, "instance_id": eligible_ids[i % len(eligible_ids)]}
            for i, a in enumerate(valid_actions)
        ]

        llm_response = valid_actions_with_ids + invalid_tool_actions + ineligible_id_actions
        state = {}  # validate_actions does not use state for its logic

        result = validate_actions(llm_response, state, eligible_ids)

        eligible_set = set(eligible_ids)
        for action in result:
            self.assertIn(
                action["tool"],
                ALLOWED_TOOLS,
                f"Output contained action with disallowed tool: {action['tool']!r}",
            )
            self.assertIn(
                action["instance_id"],
                eligible_set,
                f"Output contained action targeting ineligible ID: {action['instance_id']!r}",
            )

    @given(
        eligible_ids=st.lists(_instance_id_st, min_size=1, max_size=8, unique=True),
        # All actions use an invalid tool name.
        all_invalid=st.lists(
            st.fixed_dictionaries(
                {
                    "tool": _invalid_tool_st,
                    "instance_id": _instance_id_st,
                    "reasoning": _reasoning_st,
                }
            ),
            min_size=1,
            max_size=10,
        ),
    )
    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    def test_all_invalid_tools_returns_empty(self, eligible_ids, all_invalid):
        """When every action has an unknown tool, the result is always empty.

        **Validates: Requirements 8.2**
        """
        result = validate_actions(all_invalid, {}, eligible_ids)
        self.assertEqual(
            result,
            [],
            f"Expected empty result for all-invalid-tool input, got {result!r}",
        )

    @given(
        eligible_ids=st.lists(_instance_id_st, min_size=1, max_size=8, unique=True),
        num_actions=st.integers(min_value=1, max_value=10),
        tool=_valid_tool_st,
    )
    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    def test_all_ineligible_ids_returns_empty(self, eligible_ids, num_actions, tool):
        """When every action targets an instance ID not in eligible_ids, result is empty.

        **Validates: Requirements 8.3**
        """
        # Use an "x-" prefix to ensure IDs are definitely not in eligible_ids.
        bad_id = "x-000000000bad"
        actions = [
            {"tool": tool, "instance_id": bad_id, "reasoning": "test"}
            for _ in range(num_actions)
        ]
        result = validate_actions(actions, {}, eligible_ids)
        self.assertEqual(
            result,
            [],
            f"Expected empty result for all-ineligible-id input, got {result!r}",
        )


# ---------------------------------------------------------------------------
# Property 9: Real Action Cap Enforcement
# ---------------------------------------------------------------------------


class TestRealActionCapEnforcement(unittest.TestCase):
    """Property 9 — Real Action Cap Enforcement.

    **Validates: Requirements 8.4**
    """

    @given(
        eligible_ids=st.lists(_instance_id_st, min_size=1, max_size=8, unique=True),
        # More than MAX_REAL_ACTIONS_PER_CYCLE (2) real actions — test the cap.
        num_real=st.integers(min_value=3, max_value=10),
        # Any number of non-real actions (0–5).
        num_non_real=st.integers(min_value=0, max_value=5),
        real_tool=_real_tool_st,
        non_real_tool=_non_real_tool_st,
    )
    @settings(max_examples=150, suppress_health_check=[HealthCheck.too_slow])
    def test_real_action_cap_enforced(
        self, eligible_ids, num_real, num_non_real, real_tool, non_real_tool
    ):
        """
        When the input contains more than MAX_REAL_ACTIONS_PER_CYCLE real
        actions (all with valid tools and eligible IDs), the output contains
        at most MAX_REAL_ACTIONS_PER_CYCLE real actions.

        Non-real actions are not counted against the cap.

        **Validates: Requirements 8.4**
        """
        eligible_set = set(eligible_ids)

        # All real actions target the first eligible ID (guaranteed eligible).
        real_actions = [
            {"tool": real_tool, "instance_id": eligible_ids[0], "reasoning": f"real_{i}"}
            for i in range(num_real)
        ]
        # Non-real actions also target the first eligible ID.
        non_real_actions = [
            {
                "tool": non_real_tool,
                "instance_id": eligible_ids[0],
                "reasoning": f"non_real_{i}",
            }
            for i in range(num_non_real)
        ]

        llm_response = real_actions + non_real_actions
        result = validate_actions(llm_response, {}, eligible_ids)

        # Count real actions in the result.
        real_in_result = [a for a in result if a["tool"] in REAL_ACTIONS]
        non_real_in_result = [a for a in result if a["tool"] not in REAL_ACTIONS]

        self.assertLessEqual(
            len(real_in_result),
            MAX_REAL_ACTIONS_PER_CYCLE,
            f"Expected at most {MAX_REAL_ACTIONS_PER_CYCLE} real actions, "
            f"got {len(real_in_result)}: {real_in_result!r}",
        )
        self.assertEqual(
            len(non_real_in_result),
            num_non_real,
            f"Non-real actions should be retained unrestricted; "
            f"expected {num_non_real}, got {len(non_real_in_result)}",
        )

    @given(
        eligible_ids=st.lists(_instance_id_st, min_size=1, max_size=8, unique=True),
        # Exactly MAX_REAL_ACTIONS_PER_CYCLE — should all pass through.
        num_real=st.just(MAX_REAL_ACTIONS_PER_CYCLE),
        real_tool=_real_tool_st,
    )
    @settings(max_examples=75, suppress_health_check=[HealthCheck.too_slow])
    def test_exactly_cap_real_actions_all_kept(
        self, eligible_ids, num_real, real_tool
    ):
        """Exactly MAX_REAL_ACTIONS_PER_CYCLE real actions are all kept.

        **Validates: Requirements 8.4**
        """
        real_actions = [
            {"tool": real_tool, "instance_id": eligible_ids[0], "reasoning": f"r_{i}"}
            for i in range(num_real)
        ]
        result = validate_actions(real_actions, {}, eligible_ids)
        real_in_result = [a for a in result if a["tool"] in REAL_ACTIONS]

        self.assertEqual(
            len(real_in_result),
            MAX_REAL_ACTIONS_PER_CYCLE,
            f"Expected exactly {MAX_REAL_ACTIONS_PER_CYCLE} real actions kept, "
            f"got {len(real_in_result)}",
        )

    @given(
        eligible_ids=st.lists(_instance_id_st, min_size=1, max_size=8, unique=True),
        # Fewer than MAX_REAL_ACTIONS_PER_CYCLE — all should pass.
        num_real=st.integers(min_value=0, max_value=MAX_REAL_ACTIONS_PER_CYCLE - 1),
        real_tool=_real_tool_st,
    )
    @settings(max_examples=75, suppress_health_check=[HealthCheck.too_slow])
    def test_under_cap_real_actions_all_kept(
        self, eligible_ids, num_real, real_tool
    ):
        """Fewer than MAX_REAL_ACTIONS_PER_CYCLE real actions are all kept.

        **Validates: Requirements 8.4**
        """
        real_actions = [
            {"tool": real_tool, "instance_id": eligible_ids[0], "reasoning": f"r_{i}"}
            for i in range(num_real)
        ]
        result = validate_actions(real_actions, {}, eligible_ids)
        real_in_result = [a for a in result if a["tool"] in REAL_ACTIONS]

        self.assertEqual(
            len(real_in_result),
            num_real,
            f"Expected all {num_real} real actions kept (under cap), "
            f"got {len(real_in_result)}",
        )

    @given(
        eligible_ids=st.lists(_instance_id_st, min_size=1, max_size=8, unique=True),
        # Large number of non-real actions — none should be dropped.
        num_non_real=st.integers(min_value=0, max_value=20),
        non_real_tool=_non_real_tool_st,
    )
    @settings(max_examples=75, suppress_health_check=[HealthCheck.too_slow])
    def test_non_real_actions_never_capped(
        self, eligible_ids, num_non_real, non_real_tool
    ):
        """Non-real actions (do_nothing, alert_human) are never dropped due to
        the cap — they are retained regardless of count.

        **Validates: Requirements 8.4**
        """
        non_real_actions = [
            {
                "tool": non_real_tool,
                "instance_id": eligible_ids[0],
                "reasoning": f"nr_{i}",
            }
            for i in range(num_non_real)
        ]
        result = validate_actions(non_real_actions, {}, eligible_ids)

        self.assertEqual(
            len(result),
            num_non_real,
            f"Expected all {num_non_real} non-real actions retained, "
            f"got {len(result)}: {result!r}",
        )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    unittest.main(verbosity=2)
