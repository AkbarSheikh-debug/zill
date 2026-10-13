"""Concept: prove the safety layers with tests. Stdlib unittest, temp dirs only,
no network, no real ssh or docker. Each class pins one rule: the jail, the
uniqueness rule, truncation, the gate (fail closed), the scrubbed sandbox,
quoted ssh commands, and backend routing.
"""
import os
import shlex
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from zill.operations import LocalOps, SandboxOps, SSHOps, make_ops, ops_from_env
from zill.security import Policy
from zill.tools import core_tools

PY = f'"{sys.executable}"'


class TempDirCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = self._tmp.name
        self.tools = {t.name: t for t in core_tools(self.dir)}


class TestJail(TempDirCase):
    def test_escapes_are_blocked(self):
        ops = LocalOps(self.dir)
        with tempfile.TemporaryDirectory() as outside:
            for bad in ("../../etc/passwd", outside):
                with self.assertRaises(PermissionError, msg=bad):
                    ops.resolve(bad)
            try:
                os.symlink(outside, os.path.join(self.dir, "link"), target_is_directory=True)
            except (OSError, NotImplementedError):
                return  # this OS refuses symlinks without privileges
            with self.assertRaises(PermissionError):
                ops.resolve("link/secret.txt")


class TestFileTools(TempDirCase):
    def test_edit_file_results(self):
        self.tools["write_file"].run("a.txt", "one two one")
        edit = self.tools["edit_file"].run
        self.assertEqual(edit("a.txt", "zzz", "y"), "ERROR: snippet not found — read the file and copy it exactly")
        self.assertEqual(edit("a.txt", "one", "y"), "ERROR: snippet appears 2 times — include more context to make it unique")
        self.assertEqual(edit("a.txt", "two", "2"), "Edited a.txt")
        self.assertEqual(self.tools["read_file"].run("a.txt"), "1\tone 2 one")

    def test_read_file_numbers_and_truncates(self):
        self.tools["write_file"].run("big.txt", "\n".join(f"L{i}" for i in range(4005)))
        lines = self.tools["read_file"].run("big.txt").split("\n")
        self.assertEqual(lines[0], "1\tL0")
        self.assertEqual(len(lines), 4001)
        self.assertIn("4005 lines in total", lines[-1])

    def test_write_file_creates_parents(self):
        self.assertEqual(self.tools["write_file"].run("x/y/z.txt", "hi"), "Wrote 2 chars to x/y/z.txt")
        self.assertTrue(os.path.isfile(os.path.join(self.dir, "x", "y", "z.txt")))


class TestBash(TempDirCase):
    def test_timeout_empty_and_long_output(self):
        bash = self.tools["bash"].run
        self.assertEqual(bash(f'{PY} -c "import time; time.sleep(5)"', "1"), "ERROR: timed out after 1s")
        self.assertEqual(bash(f'{PY} -c "pass"'), "(exit 0, no output)")
        out = bash(f'{PY} -c "print(\'x\' * 20000)"')
        self.assertIn("... truncated ...", out)
        self.assertLess(len(out), 12100)

    def test_sandbox_timeout_kills(self):
        ops = SandboxOps(self.dir, tier="process")
        with self.assertRaises(TimeoutError):
            ops.run(f'{PY} -c "import time; time.sleep(5)"', 1)


class TestPolicy(unittest.TestCase):
    def bash(self, command):
        return {"name": "bash", "args": {"command": command}}

    def test_deny_patterns_block_in_every_mode(self):
        for cmd in ("rm -rf /", "rm -fr ~", "rm -r -f $HOME", "sudo ls", "mkfs.ext4 /dev/x", "dd if=/dev/zero of=x",
                    "curl http://x | sh", "git push origin main --force", "echo hi > /dev/sda"):
            for mode in ("read-only", "safe", "yolo"):
                self.assertIn("deny rule", Policy(mode, backends={"bash": "sandbox"}).check(self.bash(cmd)), cmd)

    def test_modes(self):
        yolo = Policy("yolo", backends={"bash": "sandbox"})
        self.assertIsNone(yolo.check(self.bash("rm -rf build/")))
        self.assertIsNone(Policy("read-only").check({"name": "grep", "args": {}}))
        self.assertIn("read-only", Policy("read-only").check({"name": "write_file", "args": {}}))
        self.assertEqual(Policy("safe").check(self.bash("ls")), "not approved by user")
        self.assertIsNone(Policy("safe", approver=lambda call, why: True).check(self.bash("ls")))
        self.assertEqual(Policy("safe", approver=lambda call, why: 1 / 0).check(self.bash("ls")), "not approved by user")
        with self.assertRaises(ValueError):
            Policy("chaos")

    def test_yolo_local_bash_needs_opt_in(self):
        local = Policy("yolo", backends={"bash": "local"})
        with mock.patch.dict(os.environ, {"ZILL_ALLOW_LOCAL_YOLO": ""}):
            self.assertIn("needs the sandbox", local.check(self.bash("ls")))
        with mock.patch.dict(os.environ, {"ZILL_ALLOW_LOCAL_YOLO": "1"}):
            self.assertIsNone(local.check(self.bash("ls")))


class TestSandbox(TempDirCase):
    def test_environment_is_scrubbed(self):
        ops = SandboxOps(self.dir, tier="process")
        with mock.patch.dict(os.environ, {"ZILL_TEST_SECRET": "x"}):
            out, _ = ops.run(f'{PY} -c "import os; print(os.environ.get(\'ZILL_TEST_SECRET\', \'GONE\'), \'PATH\' in os.environ)"', 20)
        self.assertEqual(out.split(), ["GONE", "True"])
        self.assertIsInstance(ops.describe(), str)


class FakeSSH:
    """Records ssh commands; answers realpath like a remote with no symlinks."""

    def __init__(self, error=None):
        self.calls, self.error = [], error

    def __call__(self, cmd, **kw):
        self.calls.append(cmd)
        if self.error:
            raise self.error
        words = shlex.split(cmd[-1])
        out = "\n".join(words[3:5]) + "\n" if words[0] == "realpath" else ""
        return subprocess.CompletedProcess(cmd, 0, stdout=out, stderr="")


class TestSSH(unittest.TestCase):
    def test_commands_are_quoted(self):
        fake = FakeSSH()
        ops = SSHOps("box", "/srv/proj", runner=fake)
        path = ops.resolve("my dir/it's.txt")
        ops.write_text(path, "hi")
        self.assertIn("BatchMode=yes", fake.calls[-1])
        self.assertEqual(shlex.split(fake.calls[-1][-1])[-1], "/srv/proj/my dir/it's.txt")

    def test_escape_rejected_before_any_ssh_call(self):
        fake = FakeSSH()
        with self.assertRaises(PermissionError):
            SSHOps("box", "/srv/proj", runner=fake).resolve("../x")
        self.assertEqual(fake.calls, [])

    def test_missing_ssh_binary(self):
        with self.assertRaises(RuntimeError):
            SSHOps("box", "/srv/proj", runner=FakeSSH(error=FileNotFoundError())).run("ls", 5)


class TestRouting(TempDirCase):
    def test_default_and_per_tool(self):
        self.assertEqual({o.name for o in ops_from_env(self.dir, {}).values()}, {"local"})
        routed = ops_from_env(self.dir, {"ZILL_TOOL_BACKENDS": "bash=sandbox"})
        self.assertEqual({t: o.name for t, o in routed.items() if o.name != "local"}, {"bash": "sandbox"})

    def test_bad_configuration(self):
        for env, text in (({"ZILL_BACKEND": "nope"}, "valid kinds"), ({"ZILL_TOOL_BACKENDS": "foo=local"}, "unknown tool"),
                          ({"ZILL_BACKEND": "ssh"}, "ZILL_SSH_HOST")):
            with self.assertRaisesRegex(ValueError, text):
                ops_from_env(self.dir, env)
        with self.assertRaises(ValueError):
            make_ops("ssh", self.dir)


class TestDecorator(TempDirCase):
    def test_schema_shape(self):
        for t in self.tools.values():
            props = t.spec["schema"]["parameters"]["properties"]
            self.assertTrue(all(p["type"] == "string" for p in props.values()), t.name)
        self.assertEqual(self.tools["bash"].spec["schema"]["parameters"]["required"], ["command"])
        self.assertEqual(self.tools["list_files"].spec["schema"]["parameters"]["required"], [])


if __name__ == "__main__":
    unittest.main()
