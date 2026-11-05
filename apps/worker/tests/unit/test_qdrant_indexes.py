import io
import json

from tools.performance.create_qdrant_payload_indexes import apply_indexes


def test_payload_indexes_created_once_and_verified():
    schema = {}
    calls = []
    def opener(req, timeout):
        calls.append(req.get_method())
        if req.get_method() == "PUT":
            schema[json.loads(req.data)["field_name"]] = {"data_type": "keyword"}
            result = {"status": "ok"}
        else:
            result = {"result": {"payload_schema": schema}}
        return io.BytesIO(json.dumps(result).encode())
    assert len(apply_indexes("http://localhost:6333", "chunks", opener=opener)) == 5
    assert apply_indexes("http://localhost:6333", "chunks", opener=opener) == []
    assert calls.count("PUT") == 5
