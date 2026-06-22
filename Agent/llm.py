"""
llm.py — Token-efficient agentic decision module using Groq + LangGraph.

Token optimizations:
  - Single LLM call with compact prompt (observe + reason + decide in one shot)
  - Metrics summarized as averages — not raw arrays
  - History capped at last 5 records
  - max_tokens capped at 512 (actions are short JSON)

Key rotation:
  - Loads GROQ_API_KEY1 through GROQ_API_KEY5 from .env
  - Rotates to next key on 429 rate-limit error
  - Raises ValueError (not GroqRateLimitError) when all keys exhausted

Public API:
    call_llm(state, eligible_ids, history) -> list[dict]
"""

import json
import logging
import os
from pathlib import Path

from dotenv import load_dotenv
from groq import RateLimitError as GroqRateLimitError
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_groq import ChatGroq
from langgraph.graph import END, StateGraph
from typing import TypedDict

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Environment
# ---------------------------------------------------------------------------

load_dotenv(dotenv_path=Path(__file__).resolve().parent / ".env")

# ---------------------------------------------------------------------------
# Load up to 5 Groq keys — rotate on 429
# ---------------------------------------------------------------------------

_GROQ_KEYS: list[str] = [
    k for k in [
        os.environ.get("GROQ_API_KEY1"),
        os.environ.get("GROQ_API_KEY2"),
        os.environ.get("GROQ_API_KEY3"),
        os.environ.get("GROQ_API_KEY4"),
        os.environ.get("GROQ_API_KEY5"),
    ]
    if k
]

if not _GROQ_KEYS:
    raise EnvironmentError(
        "No Groq API keys found. Set GROQ_API_KEY1..GROQ_API_KEY5 in Agent/.env."
    )

logger.info("Loaded %d Groq API key(s).", len(_GROQ_KEYS))

_current_key_index = 0


def _get_llm() -> ChatGroq:
    return ChatGroq(
        api_key=_GROQ_KEYS[_current_key_index],
        model="llama-3.3-70b-versatile",
        temperature=0.1,
        max_tokens=512,   # actions are short — 512 is plenty
    )


def _invoke_with_rotation(messages: list):
    """Invoke LLM; rotate key on 429. Raises ValueError if all keys exhausted."""
    global _current_key_index
    last_exc = None
    for _ in range(len(_GROQ_KEYS)):
        try:
            return _get_llm().invoke(messages)
        except GroqRateLimitError as exc:
            last_exc = exc
            next_idx = (_current_key_index + 1) % len(_GROQ_KEYS)
            logger.warning(
                "Groq key %d rate-limited (429). Rotating to key %d.",
                _current_key_index + 1, next_idx + 1,
            )
            _current_key_index = next_idx

    raise ValueError(
        f"All {len(_GROQ_KEYS)} Groq keys are rate-limited. "
        f"Original error: {last_exc}"
    )


# ---------------------------------------------------------------------------
# Compact state builder — reduces input tokens significantly
# ---------------------------------------------------------------------------

def _compact_state(instances: list[dict], eligible_ids: list[str], history: list[dict]) -> str:
    """
    Build a minimal text summary of the current state.
    Avoids sending raw JSON arrays — sends averages instead.
    Estimated tokens: ~200-400 vs ~800-2000 for full JSON.
    """
    eligible_set = set(eligible_ids)
    lines = []

    for inst in instances:
        if inst["id"] not in eligible_set:
            continue
        m = inst.get("metrics", {})

        def avg(key):
            vals = m.get(key, [])
            return round(sum(vals) / len(vals), 1) if vals else 0.0

        lines.append(
            f"- {inst['id']} ({inst.get('name','?')}) "
            f"type={inst.get('instance_type','?')} "
            f"status={inst.get('status','?')} "
            f"cpu={avg('CPUUtilization')}% "
            f"disk_r={avg('DiskReadBytes')/1024/1024:.1f}MB "
            f"disk_w={avg('DiskWriteBytes')/1024/1024:.1f}MB "
            f"net_in={avg('NetworkIn')/1024/1024:.1f}MB "
            f"net_out={avg('NetworkOut')/1024/1024:.1f}MB"
            + (f" PENDING_RESIZE={inst['pending_resize']}" if inst.get('pending_resize') else "")
        )

    # Last 5 history records only
    hist_lines = []
    for r in history[-5:]:
        hist_lines.append(
            f"  cycle {r.get('cycle','?')}: {r.get('action','?')} "
            f"on {r.get('instance_name','?')}"
        )

    return (
        "ELIGIBLE INSTANCES:\n" + ("\n".join(lines) or "(none)") +
        "\n\nRECENT HISTORY (last 5):\n" + ("\n".join(hist_lines) or "(none)")
    )


# ---------------------------------------------------------------------------
# LangGraph — single node, one LLM call per cycle
# ---------------------------------------------------------------------------

class AgentState(TypedDict):
    instances:     list[dict]
    eligible_ids:  list[str]
    history:       list[dict]
    final_actions: list[dict]


_SYSTEM = SystemMessage(content=(
    "You are an AWS cost-optimization agent. "
    "Analyze the EC2 instances and decide the best action for each eligible instance. "
    "Respond with ONLY a valid JSON array. No markdown, no explanation. "
    'Each element: {"tool": "<action>", "instance_id": "<id>", "reasoning": "<brief>", "new_type": "<optional>"}. '
    "Tools: stop_instance, start_instance, resize_instance, tag_instance, do_nothing, alert_human. "
    "Max 2 real actions (stop/start/resize/tag) per response. "
    "Every eligible instance must have exactly one action.\n\n"
    "DECISION RULES:\n"
    "- CPU >= 80% (high spike): call stop_instance. The instance will be resized after stopping.\n"
    "- CPU < 10% sustained: call stop_instance to save cost.\n"
    "- Instance is STOPPED and has pending_resize set: call resize_instance with new_type=<pending_resize value>, then the instance can be started.\n"
    "- Instance is STOPPED with no pending_resize: call start_instance only if there is clear demand, otherwise do_nothing.\n"
    "- Instance is STOPPED and was just resized: call start_instance.\n"
    "- Uncertain: call alert_human.\n"
    "For resize_instance, include 'new_type' field in the action JSON with the target instance type."
))


def decide_node(state: AgentState) -> dict:
    """Single-node agent: observe + reason + decide in one compact call."""
    compact = _compact_state(state["instances"], state["eligible_ids"], state["history"])

    messages = [
        _SYSTEM,
        HumanMessage(content=(
            f"{compact}\n\n"
            "Respond with ONLY a valid JSON array of actions. No markdown."
        )),
    ]

    response = _invoke_with_rotation(messages)
    raw = response.content.strip()

    # Strip markdown fences
    if raw.startswith("```"):
        raw = raw[raw.index("\n") + 1:]
    if raw.endswith("```"):
        raw = raw[:raw.rfind("```")]
    raw = raw.strip()

    try:
        actions = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"LLM returned non-JSON output.\nRaw:\n{raw}"
        ) from exc

    return {"final_actions": actions}


def _build_agent():
    graph = StateGraph(AgentState)
    graph.add_node("decide", decide_node)
    graph.set_entry_point("decide")
    graph.add_edge("decide", END)
    return graph.compile()


_agent = _build_agent()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def call_llm(
    state: dict,
    eligible_ids: list[str],
    history: list[dict],
) -> list[dict]:
    """
    Make one compact LLM call and return the final action list.
    Uses ~300-500 tokens per call vs ~3000 for the multi-node version.

    Raises ValueError if the response cannot be parsed as a JSON array.
    """
    result = _agent.invoke({
        "instances":     state.get("instances", []),
        "eligible_ids":  eligible_ids,
        "history":       history,
        "final_actions": [],
    })
    return result["final_actions"]
