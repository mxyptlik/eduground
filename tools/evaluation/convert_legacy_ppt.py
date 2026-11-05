"""Convert legacy .ppt course files to .pptx with LibreOffice (offline, originals untouched).

Output mirrors the course tree under a separate converted root and never
overwrites originals. A per-file manifest records source/output hashes so the
step is resumable and auditable; conversions are proposals, not registry
approvals.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import uuid


def _sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _write_manifest(path: Path, payload: dict) -> None:
    temp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temp.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(temp, path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--courses-root", type=Path, default=Path("Courses"))
    parser.add_argument("--out-root", type=Path, default=Path("evaluation/course_intake/converted_ppt"))
    parser.add_argument("--soffice", default=r"C:\Program Files\LibreOffice\program\soffice.exe")
    parser.add_argument("--timeout-seconds", type=int, default=180)
    args = parser.parse_args()

    sources = sorted(path for path in args.courses_root.rglob("*")
                     if path.is_file() and path.suffix.lower() == ".ppt")
    if not sources:
        print(json.dumps({"error": "no .ppt files found", "root": str(args.courses_root)}))
        return 2
    manifest_path = args.out_root / "manifest.json"
    manifest = {"tool": "libreoffice-headless", "generated_at": datetime.now(timezone.utc).isoformat(),
                "entries": {}}
    if manifest_path.is_file():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    entries = manifest.setdefault("entries", {})

    converted = skipped = failed = 0
    with tempfile.TemporaryDirectory(prefix=".soffice-profile-") as profile:
        env_arg = f"-env:UserInstallation=file:///{Path(profile).as_posix()}"
        for source in sources:
            relative = source.relative_to(args.courses_root)
            output = args.out_root / relative.with_suffix(".pptx")
            source_hash = _sha256(source)
            existing = entries.get(str(source))
            if existing and existing.get("source_sha256") == source_hash and output.is_file():
                skipped += 1
                continue
            output.parent.mkdir(parents=True, exist_ok=True)
            command = [
                args.soffice, env_arg,
                "--headless", "--norestore", "--convert-to", "pptx",
                "--outdir", str(output.parent), str(source),
            ]
            try:
                result = subprocess.run(command, capture_output=True, text=True,
                                        timeout=args.timeout_seconds)
            except (OSError, subprocess.TimeoutExpired) as exc:
                entries[str(source)] = {"source_sha256": source_hash, "status": f"failed: {type(exc).__name__}"}
                _write_manifest(manifest_path, manifest)
                failed += 1
                continue
            if output.is_file():
                entries[str(source)] = {
                    "source_sha256": source_hash, "status": "converted",
                    "output": str(output), "output_sha256": _sha256(output),
                    "converted_at": datetime.now(timezone.utc).isoformat(),
                }
                converted += 1
            else:
                entries[str(source)] = {
                    "source_sha256": source_hash, "status": "failed",
                    "detail": (result.stdout or "")[-300:] + (result.stderr or "")[-300:],
                }
                failed += 1
            _write_manifest(manifest_path, manifest)
            print(f"[{converted + skipped + failed}/{len(sources)}] {relative} -> "
                  f"{entries[str(source)]['status']}", flush=True)

    manifest["summary"] = {"sources": len(sources), "converted": converted,
                           "skipped_already_done": skipped, "failed": failed}
    _write_manifest(manifest_path, manifest)
    print(json.dumps({"manifest": str(manifest_path), **manifest["summary"]}, indent=2))
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
