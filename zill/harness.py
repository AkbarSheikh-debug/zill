"""Concept: a harness is an object, and the class decides nothing.

Construction plugs each existing file into the socket it already has: tools from
tools.py, the gate from security.py, memory and skills into the prompt, the
sub-agent tool from subagent.py, the fold into loop's before_turn, and the session
log into the event recorder. The messages list is the only state, and two harnesses
share nothing.
Design rules: (1) no new logic here, only wiring; (2) every tool call goes through
the gate; (3) children are ephemeral (persist=False) so they never hijack resume.
"""
import os

from zill import context, loop, memory, provider, session, skills
from zill.operations import ops_from_env
from zill.security import Policy
from zill.subagent import subagent_tool
from zill.tools import Tool, core_tools


def _gated(tool, policy):
    """Wrap a tool so policy.check runs first; a reason becomes a BLOCKED result."""
    def run(**args):
        reason = policy.check({"name": tool.name, "args": args})
        return f"BLOCKED: {reason}" if reason else tool.run(**args)
    return Tool(tool.name, tool.spec, run, tool.backend)


class Harness:
    """One agent: its own model, gated tools, system prompt, messages and session file."""

    def __init__(self, workdir=".", model=None, policy=None, extra_tools=(), on_event=None,
                 budget_tokens=600_000, max_turns=80, enable_subagents=True, persist=True, depth=0):
        self.workdir = os.path.realpath(workdir)
        os.makedirs(self.workdir, exist_ok=True)
        self.model = model or os.environ.get("ZILL_MODEL") or provider.DEFAULT_MODEL
        self.on_event = on_event or (lambda message: None)
        self.budget_tokens, self.max_turns, self.persist = budget_tokens, max_turns, persist
        tools = core_tools(self.workdir, ops_from_env(self.workdir))
        self.policy = policy or Policy("safe")
        if not self.policy.backends:  # the gate needs to know where bash runs
            self.policy.backends = {t.name: t.backend for t in tools}
        tools.append(memory.remember_tool(self.workdir))
        if skills.catalog(self.workdir):
            tools.append(skills.use_skill_tool(self.workdir))
        if enable_subagents:
            def make_child(child_depth):  # ephemeral: a child session log must never hijack resume
                return Harness(self.workdir, self.model, self.policy, extra_tools, None, budget_tokens,
                               max_turns, enable_subagents, False, child_depth)
            tools.append(subagent_tool(make_child, depth))
        self.tools = {t.name: _gated(t, self.policy) for t in tools + list(extra_tools)}
        self.system = memory.build_system_prompt(self.workdir, skills.catalog_prompt(self.workdir))
        self.messages, self.session_path = [], None

    def resume(self, path=None):
        """Load a session (default: the latest, crash-repaired); True if messages were restored."""
        path = path or session.latest(self.workdir)
        if not path:
            return False
        self.session_path, self.messages = path, session.load(path)
        return bool(self.messages)

    def run(self, task):
        """Add the task to the tape, run the loop to completion, return the final text."""
        if self.persist and not self.session_path:
            self.session_path = session.new_session(self.workdir, task[:32])
        self.messages.append({"role": "user", "text": task})
        self._record(self.messages[-1])
        return loop.run_loop(self.model, self.system, self.messages, self.tools, self._record, self.max_turns,
                             before_turn=lambda m: context.compact(self.model, m, self.budget_tokens))

    def _record(self, message):
        """Append to the session log first (append is remember), then tell the listener."""
        if self.persist:
            session.append(self.session_path, message)
        self.on_event(message)
