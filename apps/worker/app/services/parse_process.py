"""Killable parser boundary. Only local document bytes are passed to the child."""
from dataclasses import asdict
import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory

from app.services.types import NormalizedDocument, NormalizedSegment


def parse_isolated(raw_bytes: bytes, filename: str, timeout: float) -> NormalizedDocument:
    with TemporaryDirectory(prefix="eduground-parse-") as directory:
        source = Path(directory) / "input"
        output = Path(directory) / "output.json"
        source.write_bytes(raw_bytes)
        try:
            subprocess.run([sys.executable, "-m", "app.services.parse_process",
                            str(source), filename, str(output)], check=True,
                           timeout=timeout, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except subprocess.TimeoutExpired as exc:
            raise TimeoutError(f"Parser exceeded {timeout:g} seconds for {filename}") from exc
        except subprocess.CalledProcessError as exc:
            raise ValueError(f"Parser failed for {filename}: {exc.stderr.decode(errors='replace')[-2000:]}") from exc
        data = json.loads(output.read_text(encoding="utf-8"))
        return NormalizedDocument(text=data["text"], metadata=data["metadata"],
                                  segments=[NormalizedSegment(**s) for s in data["segments"]])


if __name__ == "__main__":
    from app.services.parsing import _parse_source_bytes
    document = _parse_source_bytes(Path(sys.argv[1]).read_bytes(), sys.argv[2])
    Path(sys.argv[3]).write_text(json.dumps(asdict(document)), encoding="utf-8")
