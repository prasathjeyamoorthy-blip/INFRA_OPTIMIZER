"""
setup_aws.py — One-time AWS infrastructure provisioner for the Cost Optimizer Agent.

Run once from the Agent/ directory:
    python setup_aws.py

Uses the AWS credentials already present in Agent/.env.
Creates:
  - 4 t3.micro EC2 instances (web-1, web-2, worker-1, cache-1)
  - Tags each instance with project=cost-optimizer-agent and protected=true/false
"""

import os
import sys
from pathlib import Path

import boto3
from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Paths — .env lives in Agent/ alongside all other modules
# ---------------------------------------------------------------------------
ENV_FILE = Path(__file__).resolve().parent / ".env"

# ---------------------------------------------------------------------------
# Instance definitions — names and protected values declared as plain data
# ---------------------------------------------------------------------------
INSTANCE_DEFINITIONS = [
    {"name": "web-1",    "instance_type": "t3.micro", "protected": "false"},
    {"name": "web-2",    "instance_type": "t3.micro", "protected": "false"},
    {"name": "worker-1", "instance_type": "t3.micro", "protected": "false"},
    {"name": "cache-1",  "instance_type": "t3.micro", "protected": "true"},
]

PROJECT_TAG = "cost-optimizer-agent"


# ---------------------------------------------------------------------------
# Environment loading
# ---------------------------------------------------------------------------
def load_credentials() -> None:
    if ENV_FILE.exists():
        load_dotenv(dotenv_path=ENV_FILE, override=True)
    
    missing = [v for v in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_DEFAULT_REGION")
               if not os.environ.get(v)]
    if missing:
        raise EnvironmentError(
            f"Missing required environment variables: {missing}\n"
            f"Ensure they are set in {ENV_FILE}"
        )

    key_id = os.environ["AWS_ACCESS_KEY_ID"]
    print(f"      Using Access Key ID: {key_id[:8]}...{key_id[-4:]}")


# ---------------------------------------------------------------------------
# AMI lookup
# ---------------------------------------------------------------------------
def _get_latest_amazon_linux_ami(ec2_client) -> str:
    """Resolve the latest Amazon Linux 2 AMI for the current region."""
    response = ec2_client.describe_images(
        Owners=["amazon"],
        Filters=[
            {"Name": "name",                "Values": ["amzn2-ami-hvm-*-x86_64-gp2"]},
            {"Name": "state",               "Values": ["available"]},
            {"Name": "root-device-type",    "Values": ["ebs"]},
            {"Name": "virtualization-type", "Values": ["hvm"]},
        ],
    )
    images = sorted(response["Images"], key=lambda img: img["CreationDate"], reverse=True)
    if not images:
        raise RuntimeError("No Amazon Linux 2 AMIs found in the current region.")
    return images[0]["ImageId"]


# ---------------------------------------------------------------------------
# EC2 provisioning
# ---------------------------------------------------------------------------
def launch_instances(ec2_client) -> list[dict]:
    """Launch all 4 instances with tags applied at launch. Returns list of result dicts."""
    launched = []

    for defn in INSTANCE_DEFINITIONS:
        print(f"  Launching {defn['name']} ({defn['instance_type']}) ...", end=" ", flush=True)

        response = ec2_client.run_instances(
            ImageId=_get_latest_amazon_linux_ami(ec2_client),
            InstanceType=defn["instance_type"],
            MinCount=1,
            MaxCount=1,
            TagSpecifications=[
                {
                    "ResourceType": "instance",
                    "Tags": [
                        {"Key": "Name",      "Value": defn["name"]},
                        {"Key": "project",   "Value": PROJECT_TAG},
                        {"Key": "protected", "Value": defn["protected"]},
                    ],
                }
            ],
        )

        instance_id = response["Instances"][0]["InstanceId"]
        print(f"→ {instance_id}")
        launched.append({
            "instance_id": instance_id,
            "name":        defn["name"],
            "protected":   defn["protected"],
        })

    return launched


def wait_for_instances(ec2_client, instance_ids: list[str]) -> None:
    print(f"\n  Waiting for {len(instance_ids)} instance(s) to reach running state ...", end=" ", flush=True)
    waiter = ec2_client.get_waiter("instance_running")
    waiter.wait(InstanceIds=instance_ids)
    print("done.")


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
def print_summary(launched: list[dict]) -> None:
    print("\n" + "=" * 60)
    print("  PROVISIONING COMPLETE — RESOURCE SUMMARY")
    print("=" * 60)
    print("\n  EC2 Instances:")
    for inst in launched:
        print(f"    {inst['instance_id']}  name={inst['name']}  "
              f"project={PROJECT_TAG}  protected={inst['protected']}")
    print("\n  All instances are tagged and ready.")
    print("=" * 60 + "\n")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def main() -> None:
    print("Cost Optimizer Agent — AWS Infrastructure Provisioner")
    print("=" * 60)

    print("\n[1/3] Loading AWS credentials ...")
    load_credentials()

    region = os.environ["AWS_DEFAULT_REGION"]
    print(f"      Region: {region}")

    ec2 = boto3.client(
        "ec2",
        aws_access_key_id=os.environ["AWS_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["AWS_SECRET_ACCESS_KEY"],
        region_name=region,
    )

    print("\n[2/3] Launching EC2 instances ...")
    launched = launch_instances(ec2)
    instance_ids = [inst["instance_id"] for inst in launched]

    print("\n[3/3] Waiting for instances to reach running state ...")
    wait_for_instances(ec2, instance_ids)

    print_summary(launched)


if __name__ == "__main__":
    try:
        main()
    except EnvironmentError as exc:
        print(f"\n[ERROR] {exc}", file=sys.stderr)
        sys.exit(1)
    except Exception as exc:
        print(f"\n[ERROR] Provisioning failed: {exc}", file=sys.stderr)
        raise
