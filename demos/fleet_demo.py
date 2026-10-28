"""Concept: a fleet is plain code: independent harnesses on a thread pool.
Three jobs, two workers: a poem, a FizzBuzz script that is run, and a job that fails
(its folder path is a file). The failure becomes a result and the others still finish.
Run from the project root:  python -m demos.fleet_demo
"""
import os
import sys
import tempfile

from zill.fleet import run_fleet
from zill.harness import Harness
from zill.security import Policy


def main():
    """Run the fleet and print one line per result, then PASS or FAIL."""
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    os.environ.setdefault("ZILL_BACKEND", "sandbox")  # yolo + bash is only allowed off the local backend
    base = tempfile.mkdtemp(prefix="zill-fleet-")
    blocker = os.path.join(base, "not-a-folder")
    open(blocker, "w").close()  # makedirs on a path that is a file raises, so job 3 fails
    jobs = [{"name": "haiku", "workdir": os.path.join(base, "a"), "task": "Write a haiku about code into poem.txt."},
            {"name": "fizzbuzz", "workdir": os.path.join(base, "b"),
             "task": "Write fizzbuzz.py that prints FizzBuzz for 1 to 15, then run it and report the output."},
            {"name": "broken", "workdir": blocker, "task": "This job cannot even start."}]
    results = run_fleet(jobs, lambda workdir: Harness(workdir, policy=Policy("yolo")), max_workers=2)
    for r in results:
        print(f"{'ok  ' if r['ok'] else 'FAIL'} {r['name']:<9} {r['report'][:100]!r}")
    print("PASS" if results[0]["ok"] and results[1]["ok"] and not results[2]["ok"] else "FAIL")


if __name__ == "__main__":
    main()
