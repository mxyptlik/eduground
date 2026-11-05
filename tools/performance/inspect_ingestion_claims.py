"""List interrupted ingestion claims without replaying provider calls."""
import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "apps/worker"))

from redis import Redis
from app.jobs.batching import BUFFER_PREFIX
from app.settings import settings


def inspect(client):
    claims = []
    for key in client.scan_iter(match=f"{BUFFER_PREFIX}*:claimed", count=100):
        key = key.decode() if isinstance(key, bytes) else key
        refs = [json.loads(row) for row in client.lrange(key, 0, -1)]
        if refs:
            claims.append({"batch_key": key.removeprefix(BUFFER_PREFIX).removesuffix(":claimed"),
                           "job_ids": [ref.get("job_id") for ref in refs],
                           "source_ids": [ref.get("source_id") for ref in refs],
                           "count": len(refs)})
    return sorted(claims, key=lambda row: row["batch_key"])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    client = Redis.from_url(settings.redis_url, decode_responses=True, socket_timeout=3)
    claims = inspect(client)
    if args.json:
        print(json.dumps({"claim_count": sum(c["count"] for c in claims), "batches": claims}, indent=2))
    else:
        for row in claims:
            print(f'{row["batch_key"]}: {row["count"]} claimed jobs; IDs {", ".join(row["job_ids"])}')
        print(f"Interrupted claims: {sum(c['count'] for c in claims)}")
    return 1 if claims else 0


if __name__ == "__main__":
    raise SystemExit(main())
