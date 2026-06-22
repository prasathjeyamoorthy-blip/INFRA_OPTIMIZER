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
# Environment loading
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
# boto3 clients
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

_DATAPOINTS = 3  # last N data points per metric

# ---------------------------------------------------------------------------
# All 9 CloudWatch metrics to collect
# Each entry: (cloudwatch_metric_name, stat, query_id)
# ---------------------------------------------------------------------------

_METRIC_SPECS = [
    ("CPUUtilization",    "Average", "cpu_utilization"),
    ("DiskReadBytes",     "Sum",     "disk_read_bytes"),
    ("DiskReadOps",       "Sum",     "disk_read_ops"),
    ("DiskWriteBytes",    "Sum",     "disk_write_bytes"),
    ("DiskWriteOps",      "Sum",     "disk_write_ops"),
    ("NetworkIn",         "Sum",     "network_in"),
    ("NetworkOut",        "Sum",     "network_out"),
    ("NetworkPacketsIn",  "Sum",     "network_packets_in"),
    ("NetworkPacketsOut", "Sum",     "network_packets_out"),
]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def observe_all() -> dict:
    """
    Read the current state of all EC2 instances tagged project=cost-optimizer-agent
    and retrieve the last 3 data points for all 9 CloudWatch metrics per instance.

    Returns:
        {
            "instances": [
                {
                    "id":            str,
                    "name":          str,
                    "instance_type": str,
                    "status":        str,
                    "tags":          dict,
                    "metrics": {
                        "CPUUtilization":    [float, ...],
                        "DiskReadBytes":     [float, ...],
                        "DiskReadOps":       [float, ...],
                        "DiskWriteBytes":    [float, ...],
                        "DiskWriteOps":      [float, ...],
                        "NetworkIn":         [float, ...],
                        "NetworkOut":        [float, ...],
                        "NetworkPacketsIn":  [float, ...],
                        "NetworkPacketsOut": [float, ...]
                    }
                },
                ...
            ]
        }

    EC2 exceptions propagate to the Orchestrator's try/except.
    CloudWatch failures per instance return an empty metrics dict.
    """
    ec2_response = _ec2.describe_instances(
        Filters=[{"Name": "tag:project", "Values": ["cost-optimizer-agent"]}]
    )

    instances = []

    for reservation in ec2_response.get("Reservations", []):
        for raw in reservation.get("Instances", []):
            raw_tags = raw.get("Tags") or []
            tags = {t["Key"]: t["Value"] for t in raw_tags}

            instance_id   = raw["InstanceId"]
            name          = tags.get("Name", "")
            instance_type = raw.get("InstanceType", "")
            status        = raw.get("State", {}).get("Name", "")

            # Skip terminated and shutting-down instances entirely
            if status.lower() in ("terminated", "shutting-down"):
                continue

            metrics = _fetch_metrics(instance_id)

            instances.append({
                "id":            instance_id,
                "name":          name,
                "instance_type": instance_type,
                "status":        status,
                "tags":          tags,
                "metrics":       metrics,
            })

    return {"instances": instances}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _fetch_metrics(instance_id: str) -> dict:
    """
    Fetch the last _DATAPOINTS data points for all 9 metrics from AWS/EC2
    namespace, dimensioned by InstanceId with a 300-second period.

    Returns a dict with all 9 metric names as keys and list[float] as values.
    Returns all empty lists if CloudWatch raises.
    """
    end_time   = datetime.now(tz=timezone.utc)
    start_time = end_time - timedelta(hours=1)

    queries = [
        {
            "Id": query_id,
            "MetricStat": {
                "Metric": {
                    "Namespace":  "AWS/EC2",
                    "MetricName": metric_name,
                    "Dimensions": [{"Name": "InstanceId", "Value": instance_id}],
                },
                "Period": 300,
                "Stat":   stat,
            },
            "ReturnData": True,
        }
        for metric_name, stat, query_id in _METRIC_SPECS
    ]

    # Build empty result keyed by metric name — filled in below
    result = {metric_name: [] for metric_name, _, _ in _METRIC_SPECS}

    try:
        response = _cloudwatch.get_metric_data(
            MetricDataQueries=queries,
            StartTime=start_time,
            EndTime=end_time,
            ScanBy="TimestampDescending",
        )

        # Build a lookup: query_id → metric_name
        id_to_name = {qid: mname for mname, _, qid in _METRIC_SPECS}

        for metric_result in response.get("MetricDataResults", []):
            qid    = metric_result["Id"]
            values = list(reversed(metric_result.get("Values", [])))
            limited = values[-_DATAPOINTS:] if len(values) > _DATAPOINTS else values
            metric_name = id_to_name.get(qid)
            if metric_name:
                result[metric_name] = limited

    except Exception:
        pass  # return all-empty on CloudWatch failure

    return result
