"""Concept: orchestration is plain code. A fleet is a thread pool of independent harnesses.

Design rules: (1) jobs share nothing: each gets its own harness from make_harness;
(2) one failing job never stops the others; (3) results come back in input order.
"""
from concurrent.futures import ThreadPoolExecutor


def run_fleet(jobs, make_harness, max_workers=4):
    """Run [{name, workdir, task}] jobs in parallel; return [{name, ok, report}] in input order."""
    def one(job):
        try:
            return {"name": job["name"], "ok": True, "report": make_harness(job["workdir"]).run(job["task"])}
        except Exception as e:  # a failing job becomes a result, never an exception
            return {"name": job["name"], "ok": False, "report": f"{type(e).__name__}: {e}"}

    # The worker limit protects the provider's rate limit and your bill, not the CPU:
    # the threads mostly wait on the network. pool.map yields results in INPUT order,
    # unlike as_completed, which yields in finish order.
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        return list(pool.map(one, jobs))
