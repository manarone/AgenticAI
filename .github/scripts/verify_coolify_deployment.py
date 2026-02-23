#!/usr/bin/env python3
"""Verify Coolify deployment outcome for a specific commit."""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

SUCCESS_STATUSES = {"success", "succeeded", "completed", "finished", "done", "deployed"}
FAILURE_STATUSES = {"failed", "error", "errored", "cancelled", "canceled", "rolled_back"}
PENDING_STATUSES = {
    "queued",
    "pending",
    "running",
    "in_progress",
    "processing",
    "starting",
    "pulling",
    "building",
    "deploying",
}


def _get_json(url: str, token: str) -> dict[str, object]:
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _normalize_status(status: object) -> str:
    return str(status or "").strip().lower()


def _find_deployment(
    deployments: list[dict[str, object]],
    commit_sha: str,
) -> dict[str, object] | None:
    for deployment in deployments:
        if str(deployment.get("commit", "")).startswith(commit_sha):
            return deployment
    return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api-base")
    parser.add_argument("--webhook")
    parser.add_argument("--token", required=True)
    parser.add_argument("--app-uuid", required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--initial-wait", type=int, default=60)
    parser.add_argument("--poll-interval", type=int, default=30)
    parser.add_argument("--timeout", type=int, default=600)
    args = parser.parse_args()

    if bool(args.api_base) == bool(args.webhook):
        print("::error::Provide exactly one of --api-base or --webhook.")
        return 1

    if args.api_base:
        api_base = args.api_base.rstrip("/")
    else:
        parsed = urllib.parse.urlparse(args.webhook)
        if not parsed.scheme or not parsed.netloc:
            print("::error::Invalid webhook URL provided.")
            return 1
        api_base = f"{parsed.scheme}://{parsed.netloc}/api/v1"

    start = time.time()

    print(f"Waiting {args.initial_wait}s before polling Coolify deployments...")
    time.sleep(args.initial_wait)

    while True:
        elapsed = int(time.time() - start)
        if elapsed >= args.timeout:
            print(
                f"::error::Timed out after {args.timeout}s waiting for Coolify deployment "
                f"and healthy app state for commit {args.commit}."
            )
            return 1

        deployments_url = f"{api_base}/deployments/applications/{args.app_uuid}"
        try:
            deployments_payload = _get_json(deployments_url, args.token)
        except urllib.error.URLError as exc:
            print(f"::warning::Coolify API unreachable ({exc}). Retrying...")
            time.sleep(args.poll_interval)
            continue

        deployments = deployments_payload.get("deployments", [])
        if not isinstance(deployments, list):
            print("::warning::Unexpected deployments payload. Retrying...")
            time.sleep(args.poll_interval)
            continue

        deployment = _find_deployment(deployments, args.commit)
        if deployment is None:
            print(
                f"No deployment found yet for commit {args.commit}. "
                f"Elapsed: {elapsed}s/{args.timeout}s"
            )
            time.sleep(args.poll_interval)
            continue

        status = _normalize_status(deployment.get("status"))
        deployment_id = deployment.get("id", "unknown")
        print(f"Deployment {deployment_id} for {args.commit} has status: {status}")

        if status in FAILURE_STATUSES:
            print(f"::error::Coolify deployment failed with status '{status}'.")
            return 1

        if status in PENDING_STATUSES or not status:
            time.sleep(args.poll_interval)
            continue

        app_url = f"{api_base}/applications/{args.app_uuid}"
        try:
            app_payload = _get_json(app_url, args.token)
        except urllib.error.URLError as exc:
            print(f"::warning::Unable to verify final app status ({exc}). Retrying...")
            time.sleep(args.poll_interval)
            continue

        app_status = _normalize_status(app_payload.get("status"))
        if app_status != "running:healthy":
            print(
                f"Deployment status '{status}', app status '{app_status}'. "
                f"Waiting for 'running:healthy'. Elapsed: {elapsed}s/{args.timeout}s"
            )
            time.sleep(args.poll_interval)
            continue

        if status not in SUCCESS_STATUSES:
            print(
                f"::warning::Deployment status '{status}' is not a known success label, "
                "but app is healthy. Treating as success."
            )

        print("Coolify deployment verified successfully.")
        return 0


if __name__ == "__main__":
    sys.exit(main())
