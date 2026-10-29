"""Concept: prove the wiring. provider.complete is replaced by a scripted fake model,
so these tests need no network: the gate blocks in safe mode, yolo writes, a session
resumes, a sub-agent leaves one log, the fleet keeps order, and the CLI exits 0.
"""
import contextlib
import io
import os
import tempfile
import time
import unittest
from unittest import mock

from zill import provider
from zill.client import main
from zill.fleet import run_fleet
from zill.harness import Harness
from zill.security import Policy


def reply(text="", *calls):
    return {"text": text, "tool_calls": [{"name": n, "args": a, "extra": {}} for n, a in calls], "usage": {}}


class FakeModel:
    """Scripted model: each call returns the next reply."""

    def __init__(self, *replies):
        self.replies = list(replies)

    def __call__(self, model, system, messages, tools):
        return self.replies.pop(0)


class HarnessCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = self._tmp.name

    def script(self, *replies):
        patcher = mock.patch.object(provider, "complete", FakeModel(*replies))
        patcher.start()
        self.addCleanup(patcher.stop)

    def sessions(self):
        folder = os.path.join(self.dir, ".zill", "sessions")
        return [n for n in os.listdir(folder) if n.endswith(".jsonl")] if os.path.isdir(folder) else []


class TestGate(HarnessCase):
    write = reply("", ("write_file", {"path": "a.txt", "content": "x"}))

    def test_safe_mode_without_approver_blocks(self):
        self.script(self.write, reply("done"))
        h = Harness(self.dir, policy=Policy("safe"), persist=False)
        self.assertEqual(h.run("write a.txt"), "done")
        self.assertEqual(h.messages[2]["text"], "BLOCKED: not approved by user")
        self.assertFalse(os.path.exists(os.path.join(self.dir, "a.txt")))

    def test_yolo_mode_writes(self):
        self.script(self.write, reply("done"))
        Harness(self.dir, policy=Policy("yolo"), persist=False).run("write a.txt")
        self.assertTrue(os.path.exists(os.path.join(self.dir, "a.txt")))


class TestSession(HarnessCase):
    def test_resume_restores_messages(self):
        self.script(reply("hello"))
        first = Harness(self.dir)
        first.run("say hi")
        second = Harness(self.dir)
        self.assertTrue(second.resume())
        self.assertEqual(second.messages, first.messages)
        self.assertFalse(Harness(tempfile.mkdtemp()).resume())

    def test_subagent_leaves_exactly_one_session_file(self):
        self.script(reply("", ("spawn_agent", {"task": "make c.txt"})),  # parent delegates
                    reply("", ("write_file", {"path": "c.txt", "content": "x"})),  # child works
                    reply("child done"),  # child reports
                    reply("parent done"))  # parent finishes
        h = Harness(self.dir, policy=Policy("yolo"))
        self.assertEqual(h.run("delegate it"), "parent done")
        self.assertTrue(os.path.exists(os.path.join(self.dir, "c.txt")))
        self.assertEqual(h.messages[2]["text"], "child done")
        self.assertEqual(len(self.sessions()), 1)


class FakeHarness:
    def __init__(self, workdir):
        self.workdir = workdir

    def run(self, task):
        if task == "boom":
            raise ValueError("boom")
        time.sleep(0.2 if task == "slow" else 0)
        return f"did {task}"


class TestFleet(unittest.TestCase):
    def test_order_and_failure_isolation(self):
        jobs = [{"name": "a", "workdir": "x", "task": "slow"}, {"name": "b", "workdir": "x", "task": "boom"},
                {"name": "c", "workdir": "x", "task": "fast"}]
        results = run_fleet(jobs, FakeHarness, max_workers=3)
        self.assertEqual([r["name"] for r in results], ["a", "b", "c"])  # input order, not finish order
        self.assertEqual([r["ok"] for r in results], [True, False, True])
        self.assertEqual(results[1]["report"], "ValueError: boom")
        self.assertEqual(results[2]["report"], "did fast")


class TestClient(HarnessCase):
    def test_headless_run_returns_zero(self):
        self.script(reply("hello from the model"))
        out = io.StringIO()
        with mock.patch.dict(os.environ), contextlib.redirect_stdout(out):  # restore ZILL_BACKEND afterwards
            os.environ.pop("ZILL_BACKEND", None)
            code = main(["-p", "say hi", "-d", self.dir])
            self.assertEqual(os.environ["ZILL_BACKEND"], "sandbox")  # headless defaults to the sandbox
        self.assertEqual(code, 0)
        self.assertIn("hello from the model", out.getvalue())


if __name__ == "__main__":
    unittest.main()
