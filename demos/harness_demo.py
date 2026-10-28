"""Concept: one object does what lecture 5's demo wired by hand: persist, kill, resume.
Run from the project root:
  python -m demos.harness_demo start <workdir>    (kill it partway)
  python -m demos.harness_demo resume <workdir>
"""
import os
import sys

from zill.client import show
from zill.harness import Harness
from zill.security import Policy

TASK = "Create part1.txt through part5.txt one at a time, then SUMMARY.md describing each"
FILES = [f"part{i}.txt" for i in range(1, 6)] + ["SUMMARY.md"]


def main(mode, workdir):
    """Start or resume a run on one Harness; print PASS when all six files exist."""
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    os.environ.setdefault("ZILL_BACKEND", "sandbox")  # yolo + bash is only allowed off the local backend
    harness = Harness(workdir, policy=Policy("yolo"), on_event=show)
    if mode == "start":
        print(f"pid {os.getpid()} - kill it with Ctrl+C, `kill -9 {os.getpid()}` (macOS/Linux), "
              f"or `taskkill /F /PID {os.getpid()}` (Windows)", flush=True)
        harness.run(TASK)
    else:
        print("resumed" if harness.resume() else "nothing to resume")
        harness.run("continue the task")
    print("PASS" if all(os.path.exists(os.path.join(harness.workdir, f)) for f in FILES) else "FAIL")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
