"""Concept: prove the long-term parts. Memory (two clocks), skills (catalog vs body),
sessions (append is remember, torn tail, repair) and sub-agents (depth cap).
Stdlib unittest, temp directories only, no network.
"""
import os
import tempfile
import time
import unittest

from zill import session
from zill.memory import build_system_prompt, remember, remember_tool
from zill.skills import catalog, catalog_prompt, read_skill, use_skill_tool
from zill.subagent import subagent_tool


class TempDirCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = self._tmp.name


class TestMemory(TempDirCase):
    def test_prompt_gains_memory_only_after_remember(self):
        self.assertNotIn("Project memory", build_system_prompt(self.dir))
        self.assertEqual(remember(self.dir, "  run tests with make test  "), "Remembered in ZILL.md")
        remember(self.dir, "use tabs in Makefiles")
        prompt = build_system_prompt(self.dir, "EXTRA-SECTION")
        self.assertEqual(prompt.count("run tests with make test"), 1)
        self.assertEqual(prompt.count("use tabs in Makefiles"), 1)
        self.assertIn("Project memory (ZILL.md):", prompt)
        self.assertTrue(prompt.endswith("EXTRA-SECTION"))
        self.assertIn("working directory:", prompt)

    def test_remember_tool_shape(self):
        t = remember_tool(self.dir)
        self.assertEqual(t.name, "remember")
        self.assertEqual(t.spec["schema"]["name"], "remember")
        self.assertEqual(t.spec["schema"]["parameters"]["required"], ["note"])
        t.run("a fact")
        with open(os.path.join(self.dir, "ZILL.md"), encoding="utf-8") as f:
            self.assertEqual(f.read(), "- a fact\n")


class TestSkills(TempDirCase):
    def make_skill(self, name, text):
        os.makedirs(os.path.join(self.dir, "skills", name))
        with open(os.path.join(self.dir, "skills", name, "SKILL.md"), "w", encoding="utf-8") as f:
            f.write(text)

    def test_catalog_prompt_and_read(self):
        self.assertEqual(catalog_prompt(self.dir), "")
        self.make_skill("zeta", "\n\nFirst body line\nmore")
        self.make_skill("alpha", "---\nname: alpha\ndescription: Does alpha things\n---\nbody text")
        self.assertEqual(list(catalog(self.dir)), ["alpha", "zeta"])
        self.assertEqual(catalog(self.dir)["alpha"]["description"], "Does alpha things")
        self.assertEqual(catalog(self.dir)["zeta"]["description"], "First body line")
        prompt = catalog_prompt(self.dir)
        self.assertTrue(prompt.startswith("Skills available (load one with the use_skill tool when relevant):"))
        self.assertIn("- alpha: Does alpha things", prompt)
        self.assertIn("body text", read_skill(self.dir, "alpha"))
        self.assertEqual(read_skill(self.dir, "nope"), "ERROR: no skill named nope. Available: alpha, zeta")
        self.assertEqual(use_skill_tool(self.dir).run("nope"), read_skill(self.dir, "nope"))


class TestSession(TempDirCase):
    msgs = [{"role": "user", "text": "héllo"},
            {"role": "assistant", "text": "", "tool_calls": [{"name": "bash", "args": {"command": "ls"}}]},
            {"role": "tool", "name": "bash", "text": "a.txt"}]

    def test_append_load_round_trip(self):
        path = session.new_session(self.dir, "My Task! With *weird* chars " + "x" * 80)
        self.assertTrue(os.path.basename(path).endswith(".jsonl"))
        self.assertLessEqual(len(os.path.basename(path).split("-", 1)[1]), 40 + len(".jsonl"))
        for m in self.msgs:
            session.append(path, m)
        self.assertEqual(session.load(path), self.msgs)

    def test_torn_tail_is_dropped(self):
        path = session.new_session(self.dir)
        for m in self.msgs:
            session.append(path, m)
        with open(path, "a", encoding="utf-8") as f:
            f.write('{"role": "assistant", "te')  # the crash cut this line in half
        self.assertEqual(session.load(path), self.msgs)

    def test_repair_adds_only_the_missing_results(self):
        calls = [{"name": "write_file", "args": {}}, {"name": "bash", "args": {}}]
        tape = [{"role": "user", "text": "go"}, {"role": "assistant", "text": "", "tool_calls": calls},
                {"role": "tool", "name": "write_file", "text": "Wrote"}]
        fixed = session._repair(tape)
        self.assertEqual(len(fixed), 4)
        self.assertEqual(fixed[-1], {"role": "tool", "name": "bash",
                                     "text": "Interrupted before this ran (process restarted)."})
        self.assertEqual(session._repair(self.msgs), self.msgs)  # no open calls: unchanged
        self.assertEqual(tape[:3], fixed[:3])  # nothing deleted

    def test_latest(self):
        self.assertIsNone(session.latest(self.dir))
        old = session.new_session(self.dir, "old")
        session.append(old, self.msgs[0])
        os.utime(old, (time.time() - 100, time.time() - 100))
        new = os.path.join(os.path.dirname(old), "0-new.jsonl")
        session.append(new, self.msgs[0])
        self.assertEqual(session.latest(self.dir), new)


class FakeChild:
    def __init__(self, depth):
        self.depth = depth

    def run(self, task):
        return f"report from depth {self.depth}: {task}"


class TestSubagent(unittest.TestCase):
    def test_depth_cap_and_delegation(self):
        made = []
        spawn = subagent_tool(lambda depth: made.append(depth) or FakeChild(depth), depth=2, max_depth=2)
        self.assertEqual(spawn.run("task"), "ERROR: sub-agent depth limit reached; do this task yourself")
        self.assertEqual(made, [])  # make_child was never called
        spawn = subagent_tool(lambda depth: made.append(depth) or FakeChild(depth), depth=1, max_depth=2)
        self.assertEqual(spawn.run("do it"), "report from depth 2: do it")
        self.assertEqual(made, [2])
        self.assertEqual(spawn.name, "spawn_agent")
        self.assertEqual(spawn.spec["schema"]["parameters"]["required"], ["task"])


if __name__ == "__main__":
    unittest.main()
