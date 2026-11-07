import contextlib
import fcntl
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch


MOD = Path(__file__).resolve().parents[1] / "mods/herdr-lazyagent"
sys.path.insert(0, str(MOD))
spec = importlib.util.spec_from_file_location("herdr_lazyagent_snapshot", MOD / "snapshot.py")
snapshot = importlib.util.module_from_spec(spec)
spec.loader.exec_module(snapshot)


class SnapshotTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix="herdr-lazyagent-test-")

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def setUp(self):
        self.root = Path(self.temporary.name) / self._testMethodName
        self.own = self.root / "herdr/limits.json"
        self.omarchy = self.root / "omarchy/limits.json"
        patches = [
            patch.object(snapshot, "OWN_STATE", self.own),
            patch.object(snapshot, "OMARCHY_STATE", self.omarchy),
            patch.object(snapshot, "locate", return_value="/fake/lazyagent"),
            patch.dict(os.environ),
            patch.object(snapshot.bridge, "load_state", snapshot.original_load),
            patch.object(snapshot.bridge, "locate_lazyagent"),
            patch.object(snapshot.bridge, "version_of"),
            patch.object(snapshot.bridge, "LIMITS_TIMEOUT", 60),
        ]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)

    def write_state(self, path, fetched=None, until=0, agent="all"):
        state = {
            "version": 1, "agent": agent,
            "fetchedAt": time.time() if fetched is None else fetched,
            "reports": [], "errors": [], "lastGood": [],
            "backoff": {"until": until, "level": 2 if until else 0, "reason": ""},
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(state))
        return state

    def invoke(self, mode):
        output = io.StringIO()
        with patch.object(sys, "argv", ["snapshot.py", mode]), contextlib.redirect_stdout(output):
            result = snapshot.main()
        return result, json.loads(output.getvalue())

    def test_prefers_newest_matching_cache(self):
        self.write_state(self.own, fetched=100)
        self.write_state(self.omarchy, fetched=200)
        self.assertEqual(snapshot.load_best()["fetchedAt"], 200)
        self.write_state(self.omarchy, fetched=300, agent="codex")
        self.assertEqual(snapshot.load_best()["fetchedAt"], 100)

    def test_retains_backoff_from_older_snapshot(self):
        until = time.time() + 120
        self.write_state(self.own, fetched=100, until=until)
        self.write_state(self.omarchy, fetched=200)
        self.assertEqual(snapshot.load_best()["backoff"]["until"], until)

    def test_cached_mode_never_queries_or_creates_own_state(self):
        self.write_state(self.omarchy)
        before = self.omarchy.read_bytes()
        with patch.object(snapshot.bridge, "run_process", side_effect=AssertionError("provider call")):
            code, payload = self.invoke("cached")
        self.assertEqual(code, 0)
        self.assertTrue(payload["fromCache"])
        self.assertFalse(self.own.parent.exists())
        self.assertEqual(self.omarchy.read_bytes(), before)

    def test_manual_refresh_respects_backoff_and_leaves_omarchy_unchanged(self):
        self.write_state(self.omarchy, fetched=100, until=time.time() + 120)
        before = self.omarchy.read_bytes()
        with patch.object(snapshot.bridge, "run_process", side_effect=AssertionError("provider call")):
            _, payload = self.invoke("refresh")
        self.assertTrue(payload["fromCache"])
        self.assertEqual(payload["backoffLevel"], 2)
        self.assertEqual(self.omarchy.read_bytes(), before)

    def test_concurrent_refresh_returns_cache_without_waiting(self):
        self.write_state(self.own)
        with (self.own.parent / "refresh.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with patch.object(snapshot.bridge, "run_process", side_effect=AssertionError("provider call")):
                _, payload = self.invoke("refresh")
        self.assertTrue(payload["busy"])
        self.assertTrue(payload["fromCache"])

    def test_query_uses_fixed_arguments_and_22_second_budget(self):
        result = {"ok": True, "exitCode": 0, "stdout": json.dumps({
            "generated_at": "2026-01-01T12:00:00Z", "reports": [], "errors": [],
        }), "stderr": ""}
        with patch.object(snapshot.bridge, "run_process", return_value=result) as run:
            code, payload = self.invoke("refresh")
        self.assertEqual(code, 0)
        self.assertFalse(payload["fromCache"])
        run.assert_called_once_with(["/fake/lazyagent", "limits", "--json"], timeout=22)
        self.assertTrue(self.own.exists())

    def test_invalid_cache_is_ignored(self):
        self.omarchy.parent.mkdir(parents=True)
        self.omarchy.write_text("not json")
        self.assertEqual(snapshot.load_best(), {})


if __name__ == "__main__":
    unittest.main()
