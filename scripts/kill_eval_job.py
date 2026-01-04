#!/usr/bin/env python3
"""
Delete evaluation Kubernetes jobs by GitHub Actions run ID.

Examples:
  python3 scripts/kill_eval_job.py --run-id 20436147722
  python3 scripts/kill_eval_job.py --run-url https://github.com/OpenHands/evaluation/actions/runs/20436147722
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from typing import Iterable


RUN_ID_PATTERN = re.compile(r"/actions/runs/(\d+)")


def parse_run_id(run_id: str | None, run_url: str | None) -> str:
    if run_id and run_url:
        raise ValueError("Provide only one of --run-id or --run-url.")
    if run_id:
        if not run_id.isdigit():
            raise ValueError(f"Invalid run ID: {run_id}")
        return run_id
    if run_url:
        match = RUN_ID_PATTERN.search(run_url)
        if not match:
            raise ValueError(f"Could not parse run ID from URL: {run_url}")
        return match.group(1)
    raise ValueError("Provide --run-id or --run-url.")


def matches_run_id(job_name: str, run_id: str) -> bool:
    boundary_pattern = rf"(?:^|-){re.escape(run_id)}(?:-|$)"
    return re.search(boundary_pattern, job_name) is not None


def kubectl_base_cmd(context: str | None) -> list[str]:
    cmd = ["kubectl"]
    if context:
        cmd.extend(["--context", context])
    return cmd


def list_matching_jobs(run_id: str, namespace: str, context: str | None) -> list[str]:
    cmd = kubectl_base_cmd(context) + ["get", "jobs", "-n", namespace, "-o", "json"]
    try:
        output = subprocess.check_output(cmd)
    except FileNotFoundError as exc:
        raise RuntimeError("kubectl not found on PATH.") from exc
    except subprocess.CalledProcessError as exc:
        raise RuntimeError("kubectl failed to list jobs.") from exc

    payload = json.loads(output.decode("utf-8"))
    items = payload.get("items", [])
    matches = []
    for item in items:
        name = item.get("metadata", {}).get("name", "")
        if name and matches_run_id(name, run_id):
            matches.append(name)
    return matches


def delete_jobs(job_names: Iterable[str], namespace: str, context: str | None) -> None:
    cmd = kubectl_base_cmd(context) + ["delete", "job", "-n", namespace]
    cmd.extend(job_names)
    try:
        subprocess.check_call(cmd)
    except subprocess.CalledProcessError as exc:
        raise RuntimeError("kubectl failed to delete jobs.") from exc


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Delete evaluation Kubernetes jobs by GitHub Actions run ID."
    )
    parser.add_argument("--run-id", help="GitHub Actions run ID")
    parser.add_argument(
        "--run-url",
        help="GitHub Actions run URL (e.g. https://github.com/OpenHands/evaluation/actions/runs/123)",
    )
    parser.add_argument(
        "--namespace",
        default="evaluation-jobs",
        help="Kubernetes namespace to search (default: evaluation-jobs)",
    )
    parser.add_argument("--context", help="kubectl context to use")
    parser.add_argument(
        "--dry-run", action="store_true", help="List matching jobs without deleting"
    )
    args = parser.parse_args()

    try:
        run_id = parse_run_id(args.run_id, args.run_url)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    try:
        jobs = list_matching_jobs(run_id, args.namespace, args.context)
    except RuntimeError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    if not jobs:
        print(f"No jobs found matching run ID {run_id} in namespace {args.namespace}.")
        return 0

    if args.dry_run:
        print("Matching jobs:")
        for name in jobs:
            print(f"- {name}")
        return 0

    try:
        delete_jobs(jobs, args.namespace, args.context)
    except RuntimeError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    print(f"Deleted {len(jobs)} job(s) for run ID {run_id}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
