"""Persistence and state-transition tests for evaluation run artifacts."""

import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from evaluation.program.artifacts import RunStore, StepKey


CONFIG = "a" * 64
DATASET = "b" * 64
INPUT = "c" * 64


class RunStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = RunStore.create(self.root, configuration_hash=CONFIG, dataset_hash=DATASET)

    def test_unique_runs_and_resume_require_original_hashes(self):
        second = RunStore.create(self.root, configuration_hash=CONFIG, dataset_hash=DATASET)
        self.assertNotEqual(self.store.path, second.path)
        reopened = RunStore.open(
            self.root, self.store.manifest["run_id"],
            configuration_hash=CONFIG, dataset_hash=DATASET,
        )
        self.assertEqual(reopened.path, self.store.path)
        with self.assertRaisesRegex(ValueError, "configuration_hash"):
            RunStore.open(self.root, self.store.manifest["run_id"], configuration_hash="d" * 64, dataset_hash=DATASET)
        with self.assertRaisesRegex(ValueError, "dataset_hash"):
            RunStore.open(self.root, self.store.manifest["run_id"], configuration_hash=CONFIG, dataset_hash="d" * 64)
        with self.assertRaisesRegex(ValueError, "invalid run_id"):
            RunStore.open(self.root, "../outside", configuration_hash=CONFIG, dataset_hash=DATASET)

    def test_completed_step_is_immutable_and_resumed_without_another_attempt(self):
        key = StepKey("metric", "course:item-1", "faithfulness")
        started = self.store.begin_step(key, input_hash=INPUT)
        self.assertEqual((started.status, started.attempts), ("running", 1))
        done = self.store.complete_step(key, input_hash=INPUT, attempt=started.attempts, result={"score": 0.75, "rationale": "fixture"})
        self.assertEqual(done.status, "completed")
        self.assertEqual(done.result["score"], 0.75)
        reopened = RunStore.open(self.root, self.store.manifest["run_id"], configuration_hash=CONFIG, dataset_hash=DATASET)
        same = reopened.begin_step(key, input_hash=INPUT)
        self.assertEqual((same.status, same.attempts), ("completed", 1))
        with self.assertRaisesRegex(ValueError, "matching running"):
            reopened.complete_step(key, input_hash=INPUT, attempt=1, result={"score": 0.9})
        with self.assertRaisesRegex(ValueError, "input_hash"):
            reopened.begin_step(key, input_hash="d" * 64)

    def test_failure_retry_and_interrupted_recovery_are_explicit(self):
        key = StepKey("collect", "item-2")
        self.store.begin_step(key, input_hash=INPUT)
        with self.assertRaisesRegex(ValueError, "recover_interrupted"):
            self.store.begin_step(key, input_hash=INPUT)
        failed = self.store.fail_step(key, input_hash=INPUT, attempt=1, error_code="provider_timeout")
        self.assertEqual((failed.status, failed.error_code), ("failed", "provider_timeout"))
        with self.assertRaisesRegex(ValueError, "retry_failed"):
            self.store.begin_step(key, input_hash=INPUT)
        retry = self.store.begin_step(key, input_hash=INPUT, retry_failed=True)
        self.assertEqual((retry.status, retry.attempts, retry.error_code), ("running", 2, None))
        recovered = self.store.begin_step(key, input_hash=INPUT, recover_interrupted=True)
        self.assertEqual(recovered.attempts, 3)
        with self.assertRaisesRegex(ValueError, "matching running"):
            self.store.complete_step(key, input_hash=INPUT, attempt=2, result={"answer": "stale"})
        done = self.store.complete_step(key, input_hash=INPUT, attempt=3, result={"answer": "ok"})
        self.assertEqual((done.status, done.attempts), ("completed", 3))
        attempts = self.store.list_attempts(key)
        self.assertEqual([attempt.status for attempt in attempts], ["failed", "interrupted", "completed"])
        self.assertEqual(attempts[0].error_code, "provider_timeout")

    def test_invalid_completion_does_not_change_step(self):
        key = StepKey("metric", "item-3", "recall")
        self.store.begin_step(key, input_hash=INPUT)
        with self.assertRaisesRegex(ValueError, "finite JSON"):
            self.store.complete_step(key, input_hash=INPUT, attempt=1, result={"score": float("nan")})
        self.assertEqual(self.store.get_step(key).status, "running")
        with self.assertRaisesRegex(ValueError, "error_code"):
            self.store.fail_step(key, input_hash=INPUT, attempt=1, error_code="API_KEY=secret")

    def test_atomic_artifact_is_create_only_and_path_scoped(self):
        path = self.store.write_json_artifact("reports/summary.json", {"total": 1})
        self.assertEqual(path.read_text(encoding="utf-8"), '{"total":1}\n')
        with self.assertRaises(FileExistsError):
            self.store.write_json_artifact("reports/summary.json", {"total": 2})
        self.assertEqual(path.read_text(encoding="utf-8"), '{"total":1}\n')
        for name in ("../outside", "/outside", "a/../outside", "C:/outside", "a\\outside"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.store.write_artifact(name, "bad")
        self.assertFalse((self.root / "outside").exists())

    def test_concurrent_begin_only_one_starts_new_step(self):
        key = StepKey("collect", "item-4")
        def begin():
            try:
                return self.store.begin_step(key, input_hash=INPUT).status
            except ValueError:
                return "busy"

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: begin(), range(2)))
        self.assertCountEqual(results, ["running", "busy"])
        self.assertEqual(self.store.get_step(key).attempts, 1)


if __name__ == "__main__":
    unittest.main()
