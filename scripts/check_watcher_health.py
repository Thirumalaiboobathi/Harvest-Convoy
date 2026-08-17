"""Manual health check for the deployed daily watcher (see ADR-006 Decision
1, and watcher.py's docstring). Read-only by default -- does not invoke the
runtime or write anything. Run this the morning after a scheduled run to
confirm it actually fired and completed cleanly.

Usage:
    uv run python -m scripts.check_watcher_health [--cluster-id kamatchipuram]
                                                    [--invoke]

--invoke additionally makes a real (non-forced) call through the Lambda
shim, exercising the exact path EventBridge Scheduler uses daily. Off by
default so a routine health check never triggers Bedrock calls or Telegram
messages on its own.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, datetime, timedelta, timezone

import boto3
from dotenv import load_dotenv

# This script always checks the deployed AWS state, never the local
# FileStorage default -- set before importing storage's get_storage().
os.environ["HARVEST_CONVOY_STORAGE"] = "dynamo"

from harvest_convoy.storage import get_storage  # noqa: E402

REGION = "ap-south-1"
LAMBDA_FUNCTION_NAME = "harvest-convoy-watcher-invoker"
RUNTIME_LOG_GROUP = "/aws/bedrock-agentcore/runtimes/harvest_convoy_watcher-7DW91DHIBA-DEFAULT"
SCHEDULE_NAME = "harvest-convoy-daily-watch"


def check_marker(cluster_id: str) -> bool:
    storage = get_storage()
    last_run = storage.get_watcher_last_run(cluster_id)
    today = date.today().isoformat()
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    if last_run == today:
        print(f"[OK]   watcher marker: cluster={cluster_id} last_run={last_run} (today)")
        return True
    if last_run == yesterday:
        print(
            f"[WARN] watcher marker: cluster={cluster_id} last_run={last_run} "
            f"(yesterday, not today yet -- check schedule time vs. now)"
        )
        return False
    print(f"[FAIL] watcher marker: cluster={cluster_id} last_run={last_run!r} -- stale or missing")
    return False


def check_schedule_recent_activity() -> bool:
    logs = boto3.client("logs", region_name=REGION)
    since_ms = int((datetime.now(timezone.utc) - timedelta(hours=25)).timestamp() * 1000)
    resp = logs.filter_log_events(
        logGroupName=f"/aws/lambda/{LAMBDA_FUNCTION_NAME}",
        startTime=since_ms,
        filterPattern="ERROR",
    )
    errors = resp.get("events", [])
    if errors:
        print(f"[FAIL] {len(errors)} ERROR-level Lambda shim log line(s) in the last 25h:")
        for e in errors[:5]:
            print(f"       {e['message'][:200]}")
        return False

    cw = boto3.client("cloudwatch", region_name=REGION)
    stats = cw.get_metric_statistics(
        Namespace="AWS/Lambda",
        MetricName="Invocations",
        Dimensions=[{"Name": "FunctionName", "Value": LAMBDA_FUNCTION_NAME}],
        StartTime=datetime.now(timezone.utc) - timedelta(hours=25),
        EndTime=datetime.now(timezone.utc),
        Period=3600,
        Statistics=["Sum"],
    )
    total = sum(dp["Sum"] for dp in stats.get("Datapoints", []))
    if total < 1:
        print("[FAIL] no Lambda shim invocations recorded in the last 25h -- schedule may not have fired")
        return False
    print(f"[OK]   {int(total)} Lambda shim invocation(s) in the last 25h, no errors logged")
    return True


def check_runtime_errors() -> bool:
    logs = boto3.client("logs", region_name=REGION)
    since_ms = int((datetime.now(timezone.utc) - timedelta(hours=25)).timestamp() * 1000)
    try:
        resp = logs.filter_log_events(
            logGroupName=RUNTIME_LOG_GROUP,
            startTime=since_ms,
            filterPattern='"Traceback" "ERROR"',
        )
    except logs.exceptions.ResourceNotFoundException:
        print(f"[FAIL] runtime log group not found: {RUNTIME_LOG_GROUP}")
        return False
    errors = resp.get("events", [])
    if errors:
        print(f"[FAIL] {len(errors)} error/traceback line(s) in the runtime log group in the last 25h:")
        for e in errors[:5]:
            print(f"       {e['message'][:200]}")
        return False
    print("[OK]   no error/traceback lines in the runtime log group in the last 25h")
    return True


def do_invoke(cluster_id: str, season_id: str) -> bool:
    client = boto3.client("lambda", region_name=REGION)
    resp = client.invoke(
        FunctionName=LAMBDA_FUNCTION_NAME,
        Payload=json.dumps({"cluster_id": cluster_id, "season_id": season_id}).encode(),
    )
    payload = json.loads(resp["Payload"].read())
    if resp.get("FunctionError"):
        print(f"[FAIL] live invoke raised: {payload.get('errorMessage')}")
        return False
    body = json.loads(payload["body"])
    print(f"[OK]   live invoke returned: {body}")
    return body.get("status") not in (None, "error")


def main() -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cluster-id", default="kamatchipuram")
    parser.add_argument("--season-id", default="2026-kuruvai")
    parser.add_argument(
        "--invoke", action="store_true",
        help="Also make a real (non-forced) call through the Lambda shim.",
    )
    args = parser.parse_args()

    print(f"Harvest Convoy watcher health check -- {datetime.now(timezone.utc).isoformat()}")
    print()
    results = [
        check_marker(args.cluster_id),
        check_schedule_recent_activity(),
        check_runtime_errors(),
    ]
    if args.invoke:
        results.append(do_invoke(args.cluster_id, args.season_id))

    print()
    if all(results):
        print("All checks passed.")
        return 0
    print("One or more checks failed or warned -- see above.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
