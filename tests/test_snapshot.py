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

    def test_newer_legacy_cache_cannot_replace_scoped_windows_and_extras(self):
        state = self.write_state(self.own, fetched=time.time() - 60)
        state["reports"] = [snapshot.bridge.sanitize_report({
            "agent": "claude", "windows": [{"label": "7-day", "scope": "Fable"}],
            "resets": [{"left": 1}], "credits": [{"used": 0}],
        })]
        self.own.write_text(json.dumps(state))
        legacy = self.write_state(self.omarchy, until=time.time() + 120)
        legacy["reports"] = [{"agent": "claude", "windows": [{"label": "7-day"}]}]
        self.omarchy.write_text(json.dumps(legacy))
        with patch.object(snapshot.bridge, "run_process", side_effect=AssertionError("provider call")):
            _, payload = self.invoke("auto")
        self.assertEqual(payload["fetchedAt"], state["fetchedAt"])
        self.assertEqual(payload["reports"][0]["windows"][0]["scope"], "Fable")
        self.assertEqual(len(payload["reports"][0]["resets"]), 1)
        self.assertGreater(payload["backoffUntil"], time.time())

    def test_newest_complete_cache_is_still_shared(self):
        for path, fetched in ((self.own, 100), (self.omarchy, 200)):
            state = self.write_state(path, fetched=fetched)
            state["reports"] = [snapshot.bridge.sanitize_report({"agent": "codex"})]
            path.write_text(json.dumps(state))
        self.assertEqual(snapshot.load_best()["fetchedAt"], 200)

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

    def test_expanded_limits_survive_cache_and_provider_failure(self):
        report = {
            "agent": "claude", "provider": "Claude Code", "source": "Example plan",
            "windows": [
                {"label": "7-day", "used_percent": 40},
                {"label": "7-day", "scope": "Fable", "detail": "Shared model allowance",
                 "used_percent": 12, "resets_at": "2026-11-19T12:00:00Z"},
            ],
            "resets": [{
                "label": "5-hour limit reset", "left": 0, "total": 1,
                "usable": False, "status": "Already used",
                "expires_at": None, "expires_in_seconds": 0,
                "next_available_at": "2026-11-19T12:00:00Z",
                "next_available_in_seconds": 86400, "text": "0 of 1 left",
            }],
            "credits": [{
                "label": "Extra usage", "unit": "EUR", "enabled": True,
                "balance": None, "used": 0, "limit": 50,
                "status": "", "text": "€0.00 of €50.00 used",
            }],
        }
        def result(reports, errors):
            return {"exitCode": 0, "stdout": json.dumps({
                "reports": reports, "errors": errors,
            }), "stderr": ""}

        with patch.object(snapshot.bridge, "run_process", return_value=result([report], [])):
            _, fresh = self.invoke("refresh")
        actual = fresh["reports"][0]
        self.assertEqual(actual["windows"][1]["scope"], "Fable")
        self.assertEqual(actual["windows"][1]["detail"], "Shared model allowance")
        self.assertEqual(actual["resets"][0]["next_available_in_seconds"], 86400)
        self.assertEqual(actual["resets"][0]["expires_at"], "")
        self.assertIsNone(actual["credits"][0]["balance"])
        self.assertEqual(actual["credits"][0]["used"], 0)
        with patch.object(snapshot.bridge, "run_process", side_effect=AssertionError("provider call")):
            _, cached = self.invoke("auto")
        self.assertEqual(cached["reports"], fresh["reports"])
        failure = result([], [{"agent": "claude", "kind": "error", "message": "HTTP 429"}])
        with patch.object(snapshot.bridge, "run_process", return_value=failure):
            _, stale = self.invoke("refresh")
        self.assertEqual(stale["reports"][0], {**actual, "stale": True})
        self.assertGreater(stale["backoffUntil"], time.time())

    def test_older_shared_cache_without_extras_still_works(self):
        state = self.write_state(self.omarchy)
        state["reports"] = [{"agent": "codex", "windows": [{"label": "5-hour"}]}]
        self.omarchy.write_text(json.dumps(state))
        before = self.omarchy.read_bytes()
        with patch.object(snapshot.bridge, "run_process", side_effect=AssertionError("provider call")):
            _, cached = self.invoke("auto")
        self.assertEqual(cached["reports"][0]["resets"], [])
        self.assertEqual(cached["reports"][0]["credits"], [])
        self.assertEqual(cached["reports"][0]["windows"][0]["scope"], "")
        self.assertEqual(self.omarchy.read_bytes(), before)


class BridgeSanitizingTests(unittest.TestCase):
    def test_untrusted_extras_are_bounded_and_unknown_keys_are_dropped(self):
        raw = {
            "windows": "invalid", "resets": [None, "invalid"] + [{
                "label": "x" * 500, "left": -1, "total": True, "usable": "true",
                "next_available_in_seconds": float("inf"), "token": "must not pass through",
            }] * 40,
            "credits": [{"balance": -2.5, "used": float("nan"), "limit": True,
                         "enabled": "true", "text": "x" * 1000, "token": "must not pass through"}] * 40,
        }
        actual = snapshot.bridge.sanitize_report(raw)
        self.assertEqual(actual["windows"], [])
        self.assertEqual(len(actual["resets"]), snapshot.bridge.MAX_EXTRAS)
        reset = actual["resets"][0]
        self.assertEqual(len(reset["label"]), 120)
        self.assertEqual((reset["left"], reset["total"], reset["next_available_in_seconds"]), (0, 0, 0))
        self.assertFalse(reset["usable"])
        self.assertNotIn("token", reset)
        self.assertEqual(len(actual["credits"]), snapshot.bridge.MAX_EXTRAS)
        credit = actual["credits"][0]
        self.assertEqual(credit["balance"], -2.5)
        self.assertIsNone(credit["used"])
        self.assertIsNone(credit["limit"])
        self.assertFalse(credit["enabled"])
        self.assertEqual(len(credit["text"]), 600)
        self.assertNotIn("token", credit)

    def test_malformed_extra_collections_are_ignored(self):
        for value in (None, True, 42, "invalid", {"label": "invalid"}):
            with self.subTest(value=value):
                actual = snapshot.bridge.sanitize_report({"resets": value, "credits": value})
                self.assertEqual(actual["resets"], [])
                self.assertEqual(actual["credits"], [])


if __name__ == "__main__":
    unittest.main()
