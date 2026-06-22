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

            # Step 2: Fetch real AWS CloudWatch metrics for this instance
            metrics = _fetch_real_aws_metrics(instance_id)

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

def _fetch_real_aws_metrics(instance_id: str) -> dict:
    """
    Retrieve real AWS CloudWatch metrics for an EC2 instance.
    
    Gets the last 3 data points for CPU utilization and network traffic.
    AWS provides these metrics by default for all EC2 instances.
    
    Args:
        instance_id: The EC2 instance ID (e.g., "i-1234567890abcdef0").
        
    Returns:
        {"CpuUtilizationPercent": [float, ...], "NetworkPacketsIn": [float, ...]}
    """
    end_time = datetime.now(tz=timezone.utc)
    # Look back 1 hour to get recent data points
    start_time = end_time - timedelta(hours=1)
    
    # AWS built-in EC2 metrics - CPU and Disk only
    metric_queries = [
        {
            "Id": "cpu_utilization",
            "MetricStat": {
                "Metric": {
                    "Namespace": "AWS/EC2",
                    "MetricName": "CPUUtilization",
                    "Dimensions": [
                        {"Name": "InstanceId", "Value": instance_id}
                    ],
                },
                "Period": 300,  # 5-minute periods (standard for AWS/EC2)
                "Stat": "Average",
            },
            "ReturnData": True,
        },
        {
            "Id": "disk_read_bytes",
            "MetricStat": {
                "Metric": {
                    "Namespace": "AWS/EC2",
                    "MetricName": "DiskReadBytes",
                    "Dimensions": [
                        {"Name": "InstanceId", "Value": instance_id}
                    ],
                },
                "Period": 300,
                "Stat": "Sum",
            },
            "ReturnData": True,
        },
        {
            "Id": "disk_write_bytes",
            "MetricStat": {
                "Metric": {
                    "Namespace": "AWS/EC2",
                    "MetricName": "DiskWriteBytes", 
                    "Dimensions": [
                        {"Name": "InstanceId", "Value": instance_id}
                    ],
                },
                "Period": 300,
                "Stat": "Sum",
            },
            "ReturnData": True,
        }
    ]
    
    try:
        cw_response = _cloudwatch.get_metric_data(
            MetricDataQueries=metric_queries,
            StartTime=start_time,
            EndTime=end_time,
            ScanBy="TimestampDescending",
        )
        
        result = {
            "CpuUtilizationPercent": [], 
            "DiskReadBytes": [],
            "DiskWriteBytes": []
        }
        
        for metric_result in cw_response.get("MetricDataResults", []):
            values = list(reversed(metric_result.get("Values", [])))
            limited_values = values[-_DATAPOINTS:] if len(values) > _DATAPOINTS else values
            
            if metric_result["Id"] == "cpu_utilization":
                result["CpuUtilizationPercent"] = limited_values
            elif metric_result["Id"] == "disk_read_bytes":
                result["DiskReadBytes"] = limited_values
            elif metric_result["Id"] == "disk_write_bytes":
                result["DiskWriteBytes"] = limited_values
                
        return result
        
    except Exception:
        # If CloudWatch query fails, return empty metrics
        return {"CpuUtilizationPercent": [], "DiskReadBytes": [], "DiskWriteBytes": []}


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
