from pathlib import Path
from types import SimpleNamespace

from tools.performance.search_capacity import result_from_summary, run_manifest


def test_summary_requires_real_latency_and_checks(tmp_path: Path):
    path = tmp_path / "summary.json"
    path.write_text('{"metrics":{"http_req_duration{name:read}":{"values":{"p(95)":750}},'
                    '"http_req_failed":{"values":{"rate":0}},"checks":{"values":{"rate":1}}}}')
    assert result_from_summary(path)["passed"]
    path.write_text('{"metrics":{"http_req_failed":{"values":{"rate":0}}}}')
    assert not result_from_summary(path)["passed"]


def test_manifest_records_workload_without_identity_secrets(monkeypatch):
    monkeypatch.setattr("tools.performance.search_capacity._command_output", lambda _: "test-version")
    args = SimpleNamespace(base_url="http://127.0.0.1:8000", min_vus=1, max_vus=5,
                           probe_seconds=10, confirm_seconds=20, budget_seconds=300)
    manifest = run_manifest(k6="k6", args=args, identity_count=5)
    assert manifest["identity_count"] == 5
    assert manifest["provider_calls"] == 0
    assert manifest["endpoints"] == ["GET /api/notebooks", "GET /api/notebooks/{id}/sources",
                                     "GET /api/notebooks/{id}/notes"]
    assert "bearer_token" not in str(manifest)
