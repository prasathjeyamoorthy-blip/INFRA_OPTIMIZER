"""
publisher.py — Synthetic CloudWatch metric publisher

Publishes synthetic metric data for EC2 instances to CloudWatch in three
progressive phases driven by a cycle counter. Instance names are always
provided at runtime via run() — none are hardcoded in this module.

Usage (standalone):
    python Agent/publisher.py
"""

import os
import time

import boto3
from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Environment loading — must happen before building any boto3 client
# ---------------------------------------------------------------------------

load_dotenv()

_REQUIRED_ENV_VARS = ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_DEFAULT_REGION")

for _var in _REQUIRED_ENV_VARS:
    if not os.environ.get(_var):
        raise EnvironmentError(
            f"Required environment variable '{_var}' is missing or empty. "
            "Ensure it is set in your .env file."
        )

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

NAMESPACE = "CostOptimizerAgent"
METRICS = ["CpuUtilizationPercent", "LatencyMs"]

# Fixed publish interval — intentionally NOT read from POLL_INTERVAL
PUBLISH_INTERVAL = 10  # seconds

# Three phases of synthetic metric values, indexed by cycle count
PHASES = [
    # Phase 0 (cycles 0–9): low utilization
    {"CpuUtilizationPercent": 15.0, "LatencyMs": 80.0},
    # Phase 1 (cycles 10–19): medium utilization
    {"CpuUtilizationPercent": 55.0, "LatencyMs": 200.0},
    # Phase 2 (cycles 20+): high utilization
    {"CpuUtilizationPercent": 85.0, "LatencyMs": 450.0},
]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _current_phase(cycle: int) -> dict:
    """
    Return the metric value dict for the given cycle count.

    Args:
        cycle: Current cycle number (0-indexed).

    Returns:
        Phase 0 dict for cycle < 10,
        Phase 1 dict for 10 <= cycle < 20,
        Phase 2 dict for cycle >= 20.
    """
    if cycle < 10:
        return PHASES[0]
    elif cycle < 20:
        return PHASES[1]
    return PHASES[2]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def run(instance_names: list[str]) -> None:
    """
    Publish synthetic CloudWatch metrics for all given instance names in a
    continuous loop.

    Each iteration publishes every metric in METRICS for every instance name,
    using the phase values determined by the current cycle count. Sleeps
    PUBLISH_INTERVAL seconds between iterations.

    Args:
        instance_names: List of EC2 instance Name tag values to publish for.
                        Must be non-empty. No names are hardcoded here —
                        they are always passed in by the caller.
    """
    cw = boto3.client(
        "cloudwatch",
        aws_access_key_id=os.environ["AWS_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["AWS_SECRET_ACCESS_KEY"],
        region_name=os.environ["AWS_DEFAULT_REGION"],
    )

    cycle = 0

    while True:
        phase = _current_phase(cycle)

        for name in instance_names:
            for metric_name in METRICS:
                cw.put_metric_data(
                    Namespace=NAMESPACE,
                    MetricData=[
                        {
                            "MetricName": metric_name,
                            "Dimensions": [
                                {"Name": "InstanceName", "Value": name}
                            ],
                            "Value": phase[metric_name],
                            "Unit": "None",
                        }
                    ],
                )

        cycle += 1
        time.sleep(PUBLISH_INTERVAL)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    # Discover instance names directly via EC2 — avoids slow CloudWatch call at startup
    _ec2_client = boto3.client(
        "ec2",
        aws_access_key_id=os.environ["AWS_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["AWS_SECRET_ACCESS_KEY"],
        region_name=os.environ["AWS_DEFAULT_REGION"],
    )
    _resp = _ec2_client.describe_instances(
        Filters=[{"Name": "tag:project", "Values": ["cost-optimizer-agent"]}]
    )
    instance_names = []
    for _r in _resp.get("Reservations", []):
        for _i in _r.get("Instances", []):
            _tags = {t["Key"]: t["Value"] for t in (_i.get("Tags") or [])}
            _name = _tags.get("Name", "")
            if _name:
                instance_names.append(_name)

    print(f"Publishing metrics for: {instance_names}")
    run(instance_names)
