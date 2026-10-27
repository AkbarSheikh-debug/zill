"""Concept: the harness inside the harness.

A sub-agent is the same loop run again with an EMPTY tape, exposed to the parent as
one tool: one call goes out, one report comes back. The parent's context never sees
the child's tool noise.
Design rules: (1) the child gets only the brief, not the conversation; (2) depth is
capped so delegation cannot recurse forever; (3) make_child is supplied by the caller,
so this file knows nothing about models, tools or prompts.
"""
from zill.tools import tool


def subagent_tool(make_child, depth=0, max_depth=2):
    """Build spawn_agent. make_child(depth) returns any object with run(task) -> str."""
    @tool("Delegate a self-contained task to a fresh sub-agent with its own clean context. "
          "Give it a complete brief: it cannot see this conversation. Returns the sub-agent's final report.",
          task="Complete, self-contained instructions for the sub-agent")
    def spawn_agent(task):
        if depth >= max_depth:  # the depth cap: stop recursive delegation
            return "ERROR: sub-agent depth limit reached; do this task yourself"
        # Children never write session logs: only the top-level caller persists
        # (the next lecture's harness.py enforces this with persist=False).
        return make_child(depth + 1).run(task)

    return spawn_agent
