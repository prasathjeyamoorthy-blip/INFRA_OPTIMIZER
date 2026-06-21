"""
llm.py — Groq decision module.

Exposes a single public function:

    call_llm(state, eligible_ids, history) -> list[dict]

Makes exactly one Groq API call per invocation using the OpenAI-compatible
interface. The response must be a valid JSON array; markdown code fences are
stripped before parsing. If the response cannot be parsed, a ValueError is
raised for the Orchestrator's try/except to handle.
"""

import json
import os
from pathlib import Path

from groq import Groq
from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Environment loading — load from Agent/.env
# ---------------------------------------------------------------------------

_ENV_PATH = Path(__file__).resolve().parent / ".env"
load_dotenv(dotenv_path=_ENV_PATH)

_api_key = os.environ.get("GROQ_API_KEY")
if not _api_key:
    raise EnvironmentError(
        "Required environment variable 'GROQ_API_KEY' is missing or empty. "
        "Ensure it is set in your Agent/.env file."
    )

# ---------------------------------------------------------------------------
# Groq client and model
# ---------------------------------------------------------------------------

_client = Groq(api_key=_api_key)

# llama-3.3-70b-versatile: best reasoning on Groq free tier
_MODEL = "llama-3.3-70b-versatile"

# ---------------------------------------------------------------------------
# Prompt template — all dynamic data injected at call time
# ---------------------------------------------------------------------------

_SYSTEM_INSTRUCTION = (
    "You are an autonomous AWS cost-optimization agent. "
    "Your job: analyze the current EC2 instance state and metrics, then decide "
    "what infrastructure actions to take to minimize cost while maintaining "
    "required capacity. "
    "You must respond with ONLY a valid JSON array, no markdown, no explanation. "
    "Each element must have exactly these keys: tool, instance_id, reasoning."
)

_USER_PROMPT_TEMPLATE = """\
ELIGIBLE INSTANCE IDs (you may ONLY act on these):
{eligible_ids}

CURRENT STATE:
{current_state}

RECENT ACTION HISTORY (last 50 cycles):
{history}

Available tools: stop_instance, start_instance, resize_instance, tag_instance, do_nothing, alert_human.

Respond with ONLY a valid JSON array. No markdown, no explanation.
"""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def call_llm(
    state: dict,
    eligible_ids: list[str],
    history: list[dict],
) -> list[dict]:
    """
    Make exactly one call to Groq and return the parsed action list.

    Args:
        state:        The dict returned by observe_all().
        eligible_ids: Instance IDs that passed the safety filter this cycle.
        history:      Recent cycle records from get_recent_cycles().

    Returns:
        A list of action dicts, e.g.:
            [{"tool": "stop_instance", "instance_id": "i-abc", "reasoning": "..."}]

    Raises:
        ValueError: If the Groq response cannot be parsed as a JSON array.
                    The raw response text is included in the error message.
    """
    user_prompt = _USER_PROMPT_TEMPLATE.format(
        eligible_ids="\n".join(eligible_ids) if eligible_ids else "(none)",
        current_state=json.dumps(state, indent=2),
        history=json.dumps(history, indent=2),
    )

    # Exactly one API call per invocation
    response = _client.chat.completions.create(
        model=_MODEL,
        messages=[
            {"role": "system", "content": _SYSTEM_INSTRUCTION},
            {"role": "user",   "content": user_prompt},
        ],
        temperature=0.2,
        max_tokens=1024,
    )

    raw_text: str = response.choices[0].message.content

    # Strip whitespace and markdown code fences (```json ... ``` or ``` ... ```)
    cleaned = raw_text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned[cleaned.index("\n") + 1:]
    if cleaned.endswith("```"):
        cleaned = cleaned[:cleaned.rfind("```")]
    cleaned = cleaned.strip()

    # Parse the JSON array
    try:
        actions = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"Groq returned a response that could not be parsed as JSON. "
            f"Raw response:\n{raw_text}"
        ) from exc

    return actions
