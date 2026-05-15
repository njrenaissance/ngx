"""POST a managed_database provision request and (optionally) poll until terminal.

Reads the API key from FORGE_API_KEY. No fallback — the script exits 1 if unset.

Usage:
    set FORGE_API_KEY=<your-key>            # cmd.exe
    $env:FORGE_API_KEY="<your-key>"          # PowerShell
    export FORGE_API_KEY=<your-key>          # bash

    python scripts/post_resource.py
    python scripts/post_resource.py --poll
    python scripts/post_resource.py --host http://localhost:8000 --name my-db
"""

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
import uuid


def _request(method: str, url: str, api_key: str, body: dict | None = None) -> tuple[int, dict]:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--host", default="http://localhost:8000", help="Forge api base URL")
    parser.add_argument("--name", default=f"sanity-db-{uuid.uuid4().hex[:8]}", help="Resource name")
    parser.add_argument("--tier", default="dev")
    parser.add_argument("--region", default="ngx-region-1a")
    parser.add_argument("--engine", default="postgres", choices=["postgres", "mysql"])
    parser.add_argument("--size", default="small", choices=["small", "medium", "large", "xlarge"])
    parser.add_argument("--storage-gb", type=int, default=100)
    parser.add_argument("--poll", action="store_true", help="Poll /status until terminal")
    parser.add_argument("--poll-interval", type=float, default=2.0)
    parser.add_argument("--poll-timeout", type=float, default=120.0)
    args = parser.parse_args()

    api_key = os.environ.get("FORGE_API_KEY")
    if not api_key:
        print("error: FORGE_API_KEY is not set", file=sys.stderr)
        return 1

    payload = {
        "resource_type": "managed_database",
        "tier": args.tier,
        "logical_region": args.region,
        "name": args.name,
        "config": {"engine": args.engine, "size": args.size, "storage_gb": args.storage_gb},
    }

    print(f"POST {args.host}/v1/resources")
    print(json.dumps(payload, indent=2))
    code, body = _request("POST", f"{args.host}/v1/resources", api_key, payload)
    print(f"\n<- {code}")
    print(json.dumps(body, indent=2))
    if code != 202:
        return 1

    if not args.poll:
        return 0

    resource_id = body["resource_id"]
    status_url = f"{args.host}/v1/resources/{resource_id}/status"
    terminal = {"provisioned", "failed", "cancelled"}
    deadline = time.monotonic() + args.poll_timeout
    last_status = None
    print(f"\npolling {status_url} every {args.poll_interval}s (timeout {args.poll_timeout}s)")
    while time.monotonic() < deadline:
        code, body = _request("GET", status_url, api_key)
        if code != 200:
            print(f"  status probe failed: {code} {body}")
            return 1
        status = body.get("status")
        if status != last_status:
            print(f"  [{time.strftime('%H:%M:%S')}] status={status}")
            last_status = status
        if status in terminal:
            print(f"\nterminal: {status}")
            return 0 if status == "provisioned" else 1
        time.sleep(args.poll_interval)

    print(f"\ntimed out after {args.poll_timeout}s (last status: {last_status})")
    return 1


if __name__ == "__main__":
    sys.exit(main())
