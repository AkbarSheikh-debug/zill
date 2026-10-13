"""Concept: WHERE a tool runs. Tools decide WHAT happens; Operations decide WHERE.

Design rules: (1) tools never import subprocess or open files themselves; every
file or command operation goes through an Operations object. (2) One interface,
three backends (local, sandbox, ssh), so the same tools run unchanged anywhere.
(3) Every path is resolved first; the jail lives in resolve(), nowhere else.
Simplifications: files are read as UTF-8 and written with LF endings;
run() returns stdout followed by stderr, not interleaved.
"""
import os
import posixpath
import shlex
import shutil
import signal
import subprocess

try:
    import resource  # POSIX only; its absence just means no rlimits
except ImportError:
    resource = None

IGNORE = {".git", "node_modules", "__pycache__", ".venv"}
TOOL_NAMES = ("read_file", "write_file", "edit_file", "bash", "list_files", "grep")


def _escape(path):
    return PermissionError(f"{path!r} escapes the working directory")


class Operations:
    """Interface every backend meets: resolve, read_text, write_text, walk, run.

    Tools call resolve() first and pass its result to read_text/write_text, so
    the jail runs once per operation (one ssh round trip, not two).
    """

    name = "base"

    def resolve(self, path): raise NotImplementedError          # safe path or PermissionError
    def read_text(self, path): raise NotImplementedError        # file contents
    def write_text(self, path, content): raise NotImplementedError  # makes parent dirs
    def walk(self): raise NotImplementedError                   # sorted relative POSIX paths
    def run(self, command, timeout): raise NotImplementedError  # (output, exit code) or TimeoutError


class LocalOps(Operations):
    """Runs on this machine, with every path jailed inside the working directory."""

    name = "local"

    def __init__(self, workdir):
        self.root = os.path.realpath(workdir)

    def resolve(self, path):
        """The path jail: realpath flattens '..' and symlinks, then it must stay under root."""
        full = os.path.realpath(os.path.join(self.root, path))
        root, low = os.path.normcase(self.root), os.path.normcase(full)  # Windows: case-insensitive
        if low != root and not low.startswith(root + os.sep):
            raise _escape(path)
        return full

    def read_text(self, path):
        """Return the file as text (UnicodeDecodeError if it is binary)."""
        with open(path, encoding="utf-8") as f:
            return f.read()

    def write_text(self, path, content):
        """Write text, creating parent directories."""
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(content)

    def walk(self):
        """List files under the root, skipping IGNORE directories."""
        found = []
        for folder, dirs, files in os.walk(self.root):
            dirs[:] = [d for d in dirs if d not in IGNORE]
            found += [os.path.relpath(os.path.join(folder, f), self.root).replace(os.sep, "/") for f in files]
        return sorted(found)

    def run(self, command, timeout):
        """Run a shell command in the root; TimeoutError if it takes too long."""
        try:
            r = subprocess.run(command, shell=True, cwd=self.root, capture_output=True,
                               text=True, errors="replace", timeout=timeout)
        except subprocess.TimeoutExpired:
            raise TimeoutError(f"timed out after {timeout}s") from None
        return r.stdout + r.stderr, r.returncode


class SandboxOps(LocalOps):
    """A jail inside your own OS: same jailed files, but commands run in a box.

    ZILL_SANDBOX=process (default) scrubs the environment and, on POSIX, adds
    resource limits and process-group kill. That prevents ACCIDENTS (leaked
    secrets, runaway CPU, giant files, orphan processes); it is NOT a security
    boundary against a determined attacker. ZILL_SANDBOX=docker adds real
    network and filesystem isolation.
    """

    name = "sandbox"

    def __init__(self, workdir, tier=None):
        super().__init__(workdir)
        self.tier = tier or os.environ.get("ZILL_SANDBOX", "process")

    def describe(self):
        """State exactly what is enforced right now."""
        if self.tier == "docker":
            return "sandbox/docker: network blocked, only the root mounted, 512m memory, 1 cpu, 128 pids"
        limits = "active (CPU, 100 MB files, 256 fds)" if resource else "NOT available on this OS"
        return (f"sandbox/process: environment scrubbed; rlimits {limits}; "
                "network NOT blocked; not a security boundary")

    def run(self, command, timeout):
        """Run the command in the chosen tier."""
        if self.tier == "docker":
            return self._docker(command, timeout)
        env = {k: os.environ[k] for k in ("PATH", "LANG", "SYSTEMROOT", "TEMP") if k in os.environ}
        env.update(HOME=self.root, TMPDIR=self.root)  # secrets never reach the child
        posix = {"start_new_session": True, "preexec_fn": lambda: self._limit(timeout)} if resource else {}
        proc = subprocess.Popen(command, shell=True, cwd=self.root, env=env, text=True, errors="replace",
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, **posix)
        try:
            out, err = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            self._kill(proc)
            proc.communicate()
            raise TimeoutError(f"timed out after {timeout}s") from None
        return out + err, proc.returncode

    @staticmethod
    def _limit(timeout):
        for name, value in (("RLIMIT_CPU", timeout + 5), ("RLIMIT_FSIZE", 100 << 20), ("RLIMIT_NOFILE", 256)):
            try:
                resource.setrlimit(getattr(resource, name), (value, value))
            except (ValueError, OSError, AttributeError):
                pass  # best effort: a missing limit must not stop the command

    @staticmethod
    def _kill(proc):
        if resource:  # POSIX: kill the whole process group
            os.killpg(proc.pid, signal.SIGKILL)
        else:  # Windows: kill the process tree
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True)

    def _docker(self, command, timeout):
        if not shutil.which("docker"):
            raise RuntimeError("docker not found on PATH")
        cmd = ["docker", "run", "--rm", "--network", "none", "--memory", "512m", "--cpus", "1",
               "--pids-limit", "128", "-v", f"{self.root}:/work", "-w", "/work",
               os.environ.get("ZILL_SANDBOX_IMAGE", "python:3.12-slim"), "sh", "-c", command]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, errors="replace", timeout=timeout)
        except subprocess.TimeoutExpired:
            raise TimeoutError(f"timed out after {timeout}s") from None
        return r.stdout + r.stderr, r.returncode


class SSHOps(Operations):
    """Runs on a remote machine through the system ssh binary (dangerous work stays off yours)."""

    name = "ssh"

    def __init__(self, host, remote_root, ssh_args=(), runner=subprocess.run):
        self.host, self.root = host, posixpath.normpath(remote_root)
        self.ssh_args, self.runner = tuple(ssh_args), runner  # runner is injectable for tests

    def _ssh(self, remote_command, stdin=None, timeout=None):
        """The one door to the network. Host-key checking is never disabled."""
        cmd = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", *self.ssh_args, self.host, remote_command]
        try:
            r = self.runner(cmd, input=stdin, capture_output=True, text=True, errors="replace", timeout=timeout)
        except FileNotFoundError:
            raise RuntimeError("ssh not found on PATH") from None
        except subprocess.TimeoutExpired:
            raise TimeoutError(f"timed out after {timeout}s") from None
        return r.stdout + r.stderr, r.returncode

    def resolve(self, path):
        """Lexical check first (no network), then fail closed on the remote's real path."""
        full = posixpath.normpath(posixpath.join(self.root, path))
        if full != self.root and not full.startswith(self.root.rstrip("/") + "/"):
            raise _escape(path)
        out, code = self._ssh(f"realpath -m -- {shlex.quote(self.root)} {shlex.quote(full)}")
        lines = out.split("\n")
        if code != 0 or len(lines) < 2 or not (lines[1] == lines[0] or lines[1].startswith(lines[0].rstrip("/") + "/")):
            raise _escape(path)  # symlink out, or the check itself failed: treat as an escape
        return full

    def read_text(self, path):
        """Read a remote file with cat."""
        out, code = self._ssh(f"cat -- {shlex.quote(path)}")
        if code != 0:
            raise OSError(out.strip())
        return out

    def write_text(self, path, content):
        """Write a remote file; content travels on stdin, never inside the command."""
        out, code = self._ssh(f"mkdir -p {shlex.quote(posixpath.dirname(path))} && cat > {shlex.quote(path)}", stdin=content)
        if code != 0:
            raise OSError(out.strip())

    def walk(self):
        """List remote files with find, pruning IGNORE directories."""
        prune = " -o ".join(f"-name {shlex.quote(n)}" for n in sorted(IGNORE))
        out, _ = self._ssh(f"cd {shlex.quote(self.root)} && find . \\( {prune} \\) -prune -o -type f -print")
        return sorted(line[2:] for line in out.split("\n") if line.startswith("./"))

    def run(self, command, timeout):
        """Run a command in the remote root; the command is quoted into sh -c."""
        return self._ssh(f"cd {shlex.quote(self.root)} && sh -c {shlex.quote(command)}", timeout=timeout)


def make_ops(kind, root, **opts):
    """Build one Operations of the given kind ('ssh' needs host and remote_root)."""
    if kind == "local":
        return LocalOps(root)
    if kind == "sandbox":
        return SandboxOps(root)
    if kind == "ssh":
        if not (opts.get("host") and opts.get("remote_root")):
            raise ValueError("ssh backend needs host and remote_root")
        return SSHOps(opts["host"], opts["remote_root"], opts.get("ssh_args", ()))
    raise ValueError(f"unknown backend {kind!r}; valid kinds: local, ssh, sandbox")


def ops_from_env(root, env=os.environ):
    """Map each tool name to an Operations, per ZILL_BACKEND and ZILL_TOOL_BACKENDS."""
    choice = dict.fromkeys(TOOL_NAMES, env.get("ZILL_BACKEND", "local"))
    for item in filter(None, env.get("ZILL_TOOL_BACKENDS", "").split(",")):
        tool, _, kind = item.partition("=")
        if tool.strip() not in choice:
            raise ValueError(f"unknown tool {tool.strip()!r} in ZILL_TOOL_BACKENDS; valid: {', '.join(TOOL_NAMES)}")
        choice[tool.strip()] = kind.strip()
    shared = {}  # one Operations per kind
    for kind in set(choice.values()):
        opts = {}
        if kind == "ssh":
            for var in ("ZILL_SSH_HOST", "ZILL_SSH_DIR"):
                if not env.get(var):
                    raise ValueError(f"{var} is required when a tool uses the ssh backend")
            opts = {"host": env["ZILL_SSH_HOST"], "remote_root": env["ZILL_SSH_DIR"]}
        shared[kind] = make_ops(kind, root, **opts)
    return {tool: shared[kind] for tool, kind in choice.items()}
