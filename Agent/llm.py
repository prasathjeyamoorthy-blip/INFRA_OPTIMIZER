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

# llama-3.1-8b-instant: faster model with separate token limits
_MODEL = "llama-3.8-70b-versatile"

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
ELIGIBLE INSTANCES (you may ONLY act on these):
{eligible_summary}

RECENT HISTORY (last 10 cycles):
{history_summary}

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
    # Create compact summaries to reduce token usage
    eligible_summary = []
    instance_lookup = {inst["id"]: inst for inst in state.get("instances", [])}
    
    for instance_id in eligible_ids:
        inst = instance_lookup.get(instance_id, {})
        metrics = inst.get("metrics", {})
        cpu_avg = sum(metrics.get("CpuUtilizationPercent", [])) / max(1, len(metrics.get("CpuUtilizationPercent", [])))
        disk_read = sum(metrics.get("DiskReadBytes", [])) / max(1, len(metrics.get("DiskReadBytes", [])))
        disk_write = sum(metrics.get("DiskWriteBytes", [])) / max(1, len(metrics.get("DiskWriteBytes", [])))
        
        eligible_summary.append(f"{instance_id} ({inst.get('name', 'unknown')}) "
                               f"type:{inst.get('instance_type', 'unknown')} "
                               f"status:{inst.get('status', 'unknown')} "
                               f"cpu:{cpu_avg:.1f}% disk_read:{disk_read/1024/1024:.1f}MB disk_write:{disk_write/1024/1024:.1f}MB")
    
    # Compact history (last 10 actions only)
    recent_history = history[-10:] if len(history) > 10 else history
    history_summary = []
    for record in recent_history:
        history_summary.append(f"cycle {record.get('cycle', '?')}: "
                              f"{record.get('action', 'unknown')} on "
                              f"{record.get('instance_name', 'unknown')}")

    user_prompt = _USER_PROMPT_TEMPLATE.format(
        eligible_summary="\n".join(eligible_summary) if eligible_summary else "(none)",
        history_summary="\n".join(history_summary) if history_summary else "(none)",
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
