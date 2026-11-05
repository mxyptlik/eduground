"""Idempotently prepare Qdrant filters used by tutor retrieval.

This is an operator command. It does not run at every search or make model calls.
"""
import argparse
import json
import os
from urllib import error, request


def apply_indexes(base_url: str, collection: str, api_key: str | None = None, *, opener=request.urlopen):
    target = f"{base_url.rstrip('/')}/collections/{collection}"
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["api-key"] = api_key

    def call(method, url, payload=None):
        data = json.dumps(payload).encode() if payload is not None else None
        with opener(request.Request(url, data=data, headers=headers, method=method), timeout=15) as response:
            return json.loads(response.read())

    collection_data = call("GET", target)
    schema = (collection_data.get("result") or {}).get("payload_schema") or {}
    created = []
    for field in ("notebook_id", "institution_id", "source_id", "module_id", "status"):
        if field in schema:
            continue
        try:
            call("PUT", f"{target}/index?wait=true", {"field_name": field, "field_schema": "keyword"})
        except error.HTTPError as exc:
            # Concurrent migration may have created the same index. Verify it.
            current = (call("GET", target).get("result") or {}).get("payload_schema") or {}
            if field not in current:
                raise exc
        created.append(field)
    return created


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default=os.environ.get("CURRICULUM_TUTOR_QDRANT_URL", "http://localhost:6333"))
    parser.add_argument("--collection", default=os.environ.get("CURRICULUM_TUTOR_QDRANT_COLLECTION_NAME", "eduground_chunks"))
    args = parser.parse_args()
    created = apply_indexes(args.url, args.collection, os.environ.get("CURRICULUM_TUTOR_QDRANT_API_KEY"))
    print(json.dumps({"collection": args.collection, "created": created, "already_present": 5 - len(created)}))


if __name__ == "__main__":
    main()
