"""
executor.py — boto3 tool dispatch.

Implements the 6 executor tools and exposes TOOL_DISPATCH, a dict that maps
each tool name string to its callable. The Orchestrator uses TOOL_DISPATCH to
invoke validated actions without any if/else decision logic.

    TOOL_DISPATCH[action["tool"]](**action)

Each tool accepts **kwargs so it can gracefully absorb extra fields (e.g.
"reasoning", "tool") that the Orchestrator passes from the full action dict.
"""

import logging
import os
from pathlib import Path
from typing import Callable

import boto3
from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Environment loading — must happen before building any boto3 client
# ---------------------------------------------------------------------------

load_dotenv(dotenv_path=Path(__file__).resolve().parent / ".env")

_REQUIRED_ENV_VARS = ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_DEFAULT_REGION")

for _var in _REQUIRED_ENV_VARS:
    if not os.environ.get(_var):
        raise EnvironmentError(
            f"Required environment variable '{_var}' is missing or empty. "
            "Ensure it is set in your .env file."
        )

# ---------------------------------------------------------------------------
# Module-level boto3 EC2 client (built once at import time)
# ---------------------------------------------------------------------------

_ec2 = boto3.client(
    "ec2",
    aws_access_key_id=os.environ["AWS_ACCESS_KEY_ID"],
    aws_secret_access_key=os.environ["AWS_SECRET_ACCESS_KEY"],
    region_name=os.environ["AWS_DEFAULT_REGION"],
)

# ---------------------------------------------------------------------------
# Tool functions
# ---------------------------------------------------------------------------

def stop_instance(instance_id: str, **kwargs) -> dict:
    """
    Stop a running EC2 instance.

    Args:
        instance_id: The EC2 instance ID to stop.
        **kwargs:    Absorbs extra fields from the action dict (e.g. "tool",
                     "reasoning") — they are intentionally ignored here.

    Returns:
        The raw boto3 response dict from stop_instances().
    """
    return _ec2.stop_instances(InstanceIds=[instance_id])


def start_instance(instance_id: str, **kwargs) -> dict:
    """
    Start a stopped EC2 instance.

    Args:
        instance_id: The EC2 instance ID to start.
        **kwargs:    Absorbs extra fields from the action dict.

    Returns:
        The raw boto3 response dict from start_instances().
    """
    return _ec2.start_instances(InstanceIds=[instance_id])


def resize_instance(instance_id: str, new_type: str, **kwargs) -> dict:
    """
    Change the instance type of a stopped EC2 instance.

    Args:
        instance_id: The EC2 instance ID to resize.
        new_type:    The target instance type string (e.g. "t3.small").
        **kwargs:    Absorbs extra fields from the action dict.

    Returns:
        The raw boto3 response dict from modify_instance_attribute().
    """
    return _ec2.modify_instance_attribute(
        InstanceId=instance_id,
        Attribute="instanceType",
        Value=new_type,
    )


def tag_instance(instance_id: str, tags: dict, **kwargs) -> dict:
    """
    Apply or update tags on an EC2 instance.

    Args:
        instance_id: The EC2 instance ID to tag.
        tags:        A dict of {key: value} pairs to apply as EC2 tags.
        **kwargs:    Absorbs extra fields from the action dict.

    Returns:
        The raw boto3 response dict from create_tags().
    """
    ec2_tags = [{"Key": k, "Value": v} for k, v in tags.items()]
    return _ec2.create_tags(Resources=[instance_id], Tags=ec2_tags)


def do_nothing(instance_id: str, **kwargs) -> dict:
    """
    No-op action — makes no AWS API call.

    Args:
        instance_id: The EC2 instance ID (accepted for signature uniformity).
        **kwargs:    Absorbs extra fields from the action dict.

    Returns:
        {"status": "no_op"}
    """
    return {"status": "no_op"}


def alert_human(instance_id: str, message: str = "", **kwargs) -> dict:
    """
    Log an alert message for human review — makes no AWS API call.

    Args:
        instance_id: The EC2 instance ID the alert relates to.
        message:     The alert message to log at WARNING level.
        **kwargs:    Absorbs extra fields from the action dict.

    Returns:
        {"status": "alerted", "message": message}
    """
    logging.warning(message)
    return {"status": "alerted", "message": message}


# ---------------------------------------------------------------------------
# Tool dispatch table
# ---------------------------------------------------------------------------

TOOL_DISPATCH: dict[str, Callable] = {
    "stop_instance":   stop_instance,
    "start_instance":  start_instance,
    "resize_instance": resize_instance,
    "tag_instance":    tag_instance,
    "do_nothing":      do_nothing,
    "alert_human":     alert_human,
}
