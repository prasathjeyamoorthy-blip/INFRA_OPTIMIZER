"""
observer.py — AWS state reader

Reads live EC2 instance state and CloudWatch metrics for all instances
tagged project=cost-optimizer-agent. Exposes a single public function:

    observe_all() -> dict

Any boto3 exception propagates unhandled to the caller (the Orchestrator),
where it is caught by the cycle-level try/except.
"""

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import boto3
from botocore.config import Config
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
# Module-level boto3 clients (built once at import time)
# ---------------------------------------------------------------------------

_boto_config = Config(connect_timeout=10, read_timeout=20, retries={"max_attempts": 2})

_ec2 = boto3.client(
    "ec2",
    aws_access_key_id=os.environ["AWS_ACCESS_KEY_ID"],
    aws_secret_access_key=os.environ["AWS_SECRET_ACCESS_KEY"],
    region_name=os.environ["AWS_DEFAULT_REGION"],
    config=_boto_config,
)

_cloudwatch = boto3.client(
    "cloudwatch",
    aws_access_key_id=os.environ["AWS_ACCESS_KEY_ID"],
    aws_secret_access_key=os.environ["AWS_SECRET_ACCESS_KEY"],
    region_name=os.environ["AWS_DEFAULT_REGION"],
    config=_boto_config,
)

# CloudWatch metric configuration — no hardcoded instance names or IDs
_NAMESPACE = "CostOptimizerAgent"
_METRICS = ["CpuUtilizationPercent", "LatencyMs"]
_DATAPOINTS = 3  # last N data points requested per metric


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def observe_all() -> dict:
    """
    Read the current state of all EC2 instances tagged project=cost-optimizer-agent
    and retrieve the last 3 CloudWatch metric data points for each instance.

    Returns:
        {
            "instances": [
                {
                    "id":            str,   # EC2 instance ID
                    "name":          str,   # Value of the Name tag (or "")
                    "instance_type": str,
                    "status":        str,   # e.g. "running", "stopped"
                    "tags":          dict,  # {key: value, ...}
                    "metrics": {
                        "CpuUtilizationPercent": [float, ...],  # up to 3 points
                        "LatencyMs":             [float, ...]
                    }
                },
                ...
            ]
        }

    Raises:
        Any boto3 / botocore exception is propagated unhandled to the caller.
    """
    # Step 1: Describe EC2 instances filtered by the project tag
    ec2_response = _ec2.describe_instances(
        Filters=[{"Name": "tag:project", "Values": ["cost-optimizer-agent"]}]
    )

    instances = []

    for reservation in ec2_response.get("Reservations", []):
        for raw in reservation.get("Instances", []):
            # Extract flat tag dict {key: value}
            raw_tags = raw.get("Tags") or []
            tags = {t["Key"]: t["Value"] for t in raw_tags}

            instance_id = raw["InstanceId"]
            name = tags.get("Name", "")
            instance_type = raw.get("InstanceType", "")
            status = raw.get("State", {}).get("Name", "")

            # Step 2: Fetch CloudWatch metrics for this instance
            metrics = _fetch_metrics(name)

            instances.append(
                {
                    "id": instance_id,
                    "name": name,
                    "instance_type": instance_type,
                    "status": status,
                    "tags": tags,
                    "metrics": metrics,
                }
            )

    return {"instances": instances}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _fetch_metrics(instance_name: str) -> dict:
    """
    Retrieve the last _DATAPOINTS data points for each metric in _METRICS
    from CloudWatch namespace _NAMESPACE, dimensioned by instance name.

    Args:
        instance_name: The Name tag value of the EC2 instance.

    Returns:
        {"CpuUtilizationPercent": [float, ...], "LatencyMs": [float, ...]}
        Values are sorted by timestamp ascending; up to _DATAPOINTS entries each.
    """
    end_time = datetime.now(tz=timezone.utc)
    # Request a generous window so we are sure to capture the last N points
    # even with a 10-second publish interval.  1 hour is well beyond what we need.
    start_time = end_time - timedelta(hours=1)

    # Build one MetricDataQuery per tracked metric
    metric_data_queries = [
        {
            "Id": _safe_id(metric_name),
            "MetricStat": {
                "Metric": {
                    "Namespace": _NAMESPACE,
                    "MetricName": metric_name,
                    "Dimensions": [
                        {"Name": "InstanceName", "Value": instance_name}
                    ],
                },
                "Period": 10,       # matches publisher's PUBLISH_INTERVAL
                "Stat": "Average",
            },
            "ReturnData": True,
        }
        for metric_name in _METRICS
    ]

    cw_response = _cloudwatch.get_metric_data(
        MetricDataQueries=metric_data_queries,
        StartTime=start_time,
        EndTime=end_time,
        ScanBy="TimestampDescending",
    )

    result: dict[str, list[float]] = {}

    for metric_result in cw_response.get("MetricDataResults", []):
        # Map the query ID back to the original metric name
        metric_name = _id_to_metric(metric_result["Id"])
        # Values come in descending order (newest first); reverse to ascending,
        # then keep only the last _DATAPOINTS entries.
        values = list(reversed(metric_result.get("Values", [])))
        result[metric_name] = values[-_DATAPOINTS:] if len(values) > _DATAPOINTS else values

    # Ensure every metric key is present even if CloudWatch returned no data
    for metric_name in _METRICS:
        result.setdefault(metric_name, [])

    return result


def _safe_id(metric_name: str) -> str:
    """
    Convert a metric name to a valid CloudWatch query ID.
    Query IDs must start with a lowercase letter and contain only
    alphanumeric characters and underscores.
    """
    return metric_name[0].lower() + metric_name[1:].replace("-", "_")


def _id_to_metric(query_id: str) -> str:
    """Reverse the _safe_id transformation to recover the original metric name."""
    # _METRICS index lookup: find the metric whose safe_id matches
    for name in _METRICS:
        if _safe_id(name) == query_id:
            return name
    # Fallback: return the id as-is (should never happen with well-defined _METRICS)
    return query_id
