"""Tests for capture_check.py: nudge gating, transcript classification, compact."""
import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

import _util  # noqa: F401
import capture_check


def _jsonl(entries):
    return "\n".join(json.dumps(e) for e in entries) + "\n"


def _user_prompt(text="do the thing"):
    return {"type": "user",
            "message": {"role": "user",
                        "content": [{"type": "text", "text": text}]}}


def _assistant(blocks):
    return {"type": "assistant",
            "message": {"role": "assistant", "content": blocks}}


def _tool_result():
    return {"type": "user",
            "message": {"role": "user",
                        "content": [{"type": "tool_result",
                                     "tool_use_id": "t1", "content": "ok"}]}}


class TestTurnUsedTools(unittest.TestCase):
    def _classify(self, entries):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "transcript.jsonl"
            p.write_text(_jsonl(entries), encoding="utf-8")
            return capture_check._turn_used_tools(p)

    def test_turn_with_tool_use(self):
        self.assertTrue(self._classify([
            _user_prompt(),
            _assistant([{"type": "tool_use", "name": "Edit", "input": {}}]),
            _tool_result(),
            _assistant([{"type": "text", "text": "done"}]),
        ]))

    def test_conversational_turn(self):
        self.assertFalse(self._classify([
            _user_prompt("what is a monad?"),
            _assistant([{"type": "text", "text": "well..."}]),
        ]))

    def test_prior_turn_tools_do_not_leak_in(self):
        # An older turn used tools; the LAST turn did not -> False.
        self.assertFalse(self._classify([
            _user_prompt("old work"),
            _assistant([{"type": "tool_use", "name": "Write", "input": {}}]),
            _tool_result(),
            _assistant([{"type": "text", "text": "done"}]),
            _user_prompt("thanks, quick question"),
            _assistant([{"type": "text", "text": "answer"}]),
        ]))

    def test_string_content_prompt_boundary(self):
        self.assertFalse(self._classify([
            {"type": "user", "message": {"role": "user", "content": "hi"}},
            _assistant([{"type": "text", "text": "hello"}]),
        ]))

    def test_unclassifiable_returns_none(self):
        self.assertIsNone(self._classify([]))
        self.assertIsNone(
            capture_check._turn_used_tools("/definitely/missing/file.jsonl"))
        # transcript lag: a prompt with NO assistant entries after it yet
        self.assertIsNone(self._classify([_user_prompt()]))


class RunCase(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.project = Path(self._td.name)

    def _run(self, fn, payload):
        old = sys.stdin
        sys.stdin = io.StringIO(json.dumps(payload))
        out = io.StringIO()
        try:
            with contextlib.redirect_stdout(out):
                rc = fn()
        finally:
            sys.stdin = old
        return rc, out.getvalue()

    def _config(self, payload):
        (self.project / ".lore.json").write_text(json.dumps(payload),
                                                 encoding="utf-8")


class TestRunStop(RunCase):
    def test_nudges_by_default_without_transcript(self):
        rc, out = self._run(capture_check.run_stop,
                            {"cwd": str(self.project)})
        self.assertEqual(rc, 0)
        obj = json.loads(out)
        self.assertEqual(obj["decision"], "block")
        self.assertIn("LORE CHECK", obj["reason"])

    def test_stop_hook_active_suppresses(self):
        rc, out = self._run(capture_check.run_stop,
                            {"cwd": str(self.project),
                             "stop_hook_active": True})
        self.assertEqual(out, "")

    def test_off_suppresses(self):
        self._config({"captureNudge": "off"})
        _rc, out = self._run(capture_check.run_stop,
                             {"cwd": str(self.project)})
        self.assertEqual(out, "")

    def test_smart_skips_conversational_turn(self):
        t = self.project / "t.jsonl"
        t.write_text(_jsonl([
            _user_prompt("hola"),
            _assistant([{"type": "text", "text": "hola!"}]),
        ]), encoding="utf-8")
        _rc, out = self._run(capture_check.run_stop,
                             {"cwd": str(self.project),
                              "transcript_path": str(t)})
        self.assertEqual(out, "")

    def test_smart_nudges_tool_turn(self):
        t = self.project / "t.jsonl"
        t.write_text(_jsonl([
            _user_prompt(),
            _assistant([{"type": "tool_use", "name": "Edit", "input": {}}]),
            _tool_result(),
            _assistant([{"type": "text", "text": "done"}]),
        ]), encoding="utf-8")
        _rc, out = self._run(capture_check.run_stop,
                             {"cwd": str(self.project),
                              "transcript_path": str(t)})
        self.assertIn("LORE CHECK", out)

    def test_always_nudges_conversational_turn(self):
        self._config({"captureNudge": "always"})
        t = self.project / "t.jsonl"
        t.write_text(_jsonl([
            _user_prompt("hola"),
            _assistant([{"type": "text", "text": "hola!"}]),
        ]), encoding="utf-8")
        _rc, out = self._run(capture_check.run_stop,
                             {"cwd": str(self.project),
                              "transcript_path": str(t)})
        self.assertIn("LORE CHECK", out)


class TestRunCompact(RunCase):
    def test_injects_context_when_store_exists(self):
        (self.project / "learnings").mkdir()
        _rc, out = self._run(capture_check.run_compact,
                             {"cwd": str(self.project), "source": "compact"})
        obj = json.loads(out)
        hso = obj["hookSpecificOutput"]
        self.assertEqual(hso["hookEventName"], "SessionStart")
        self.assertIn("learnings", hso["additionalContext"])

    def test_silent_without_store(self):
        _rc, out = self._run(capture_check.run_compact,
                             {"cwd": str(self.project)})
        self.assertEqual(out, "")

    def test_silent_when_off(self):
        (self.project / "learnings").mkdir()
        self._config({"captureNudge": "off"})
        _rc, out = self._run(capture_check.run_compact,
                             {"cwd": str(self.project)})
        self.assertEqual(out, "")


if __name__ == "__main__":
    unittest.main()
