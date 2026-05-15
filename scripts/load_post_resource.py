"""Fire N concurrent POST /v1/resources via httpx.AsyncClient.

Reads the API key from FORGE_API_KEY. No fallback — exits 1 if unset.

Usage:
    python scripts/load_post_resource.py                       # 100 requests, 20 concurrent
    python scripts/load_post_resource.py --count 500
    python scripts/load_post_resource.py --count 100 --concurrency 50
    python scripts/load_post_resource.py --host http://localhost:8000
"""

import argparse
import asyncio
import os
import sys
import time
import uuid
from collections import Counter

import httpx


async def _post(client: httpx.AsyncClient, host: str, api_key: str, idx: int, run_id: str) -> tuple[int, float]:
    payload = {
        "resource_type": "managed_database",
        "tier": "dev",
        "logical_region": "ngx-region-1a",
        "name": f"load-{run_id}-{idx:04d}",
        "config": {"engine": "postgres", "size": "small", "storage_gb": 100},
    }
    started = time.monotonic()
    try:
        resp = await client.post(
            f"{host}/v1/resources",
            json=payload,
            headers={"Authorization": f"Bearer {api_key}"},
        )
        return resp.status_code, time.monotonic() - started
    except httpx.HTTPError as e:
        # Surface connection-level failures as a synthetic code so they show
        # up alongside HTTP statuses in the summary.
        print(f"  request {idx}: {type(e).__name__}: {e}", file=sys.stderr)
        return 0, time.monotonic() - started


async def main_async(args: argparse.Namespace, api_key: str) -> int:
    run_id = uuid.uuid4().hex[:8]
    sem = asyncio.Semaphore(args.concurrency)

    async def _bounded(client: httpx.AsyncClient, idx: int) -> tuple[int, float]:
        async with sem:
            return await _post(client, args.host, api_key, idx, run_id)

    print(f"POST {args.host}/v1/resources × {args.count} (concurrency={args.concurrency}, run_id={run_id})")
    started = time.monotonic()

    timeout = httpx.Timeout(args.timeout, connect=5.0)
    limits = httpx.Limits(max_connections=args.concurrency, max_keepalive_connections=args.concurrency)
    async with httpx.AsyncClient(timeout=timeout, limits=limits) as client:
        tasks = [_bounded(client, i) for i in range(args.count)]
        results = await asyncio.gather(*tasks)

    elapsed = time.monotonic() - started
    statuses = Counter(code for code, _ in results)
    latencies = sorted(latency for _, latency in results)
    n = len(latencies)
    p50 = latencies[n // 2]
    p95 = latencies[int(n * 0.95)] if n > 1 else latencies[0]
    p99 = latencies[int(n * 0.99)] if n > 1 else latencies[0]

    print(f"\ndone in {elapsed:.2f}s ({args.count / elapsed:.1f} req/s)")
    print("\nstatus codes:")
    for code, count in sorted(statuses.items()):
        label = "connection-error" if code == 0 else str(code)
        print(f"  {label}: {count}")
    print(f"\nlatency (s) min={latencies[0]:.3f} p50={p50:.3f} p95={p95:.3f} p99={p99:.3f} max={latencies[-1]:.3f}")

    ok = statuses.get(202, 0)
    return 0 if ok == args.count else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--host", default="http://localhost:8000")
    parser.add_argument("--count", type=int, default=100)
    parser.add_argument("--concurrency", type=int, default=20)
    parser.add_argument("--timeout", type=float, default=30.0, help="Per-request timeout (s)")
    args = parser.parse_args()

    api_key = os.environ.get("FORGE_API_KEY")
    if not api_key:
        print("error: FORGE_API_KEY is not set", file=sys.stderr)
        return 1

    return asyncio.run(main_async(args, api_key))


if __name__ == "__main__":
    sys.exit(main())
