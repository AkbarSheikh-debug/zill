"""Concept: the front door. A thin command line over one Harness.

Design rules: (1) the client owns only I/O (printing, asking, parsing flags); every
decision lives in the harness. (2) Headless runs have nobody to ask, so they default
to yolo on the sandbox backend; interactive runs default to safe and ask. (3) Ctrl+C
never loses work: the session log is already on disk.
"""
import argparse
import os

from zill.harness import Harness
from zill.security import Policy

DIM, RESET = "\033[2m", "\033[0m"


def _clip(text, limit):
    return text if len(text) <= limit else text[:limit - 1] + "…"


def show(message):
    """Print an event: assistant text plainly, tool calls as one line, results dimmed."""
    if message["role"] == "assistant":
        if message.get("text"):
            print(message["text"])
        for call in message.get("tool_calls", []):
            print(f"  -> {call['name']}({_clip(str(call['args']), 60)})")
    elif message["role"] == "tool":
        lines = (message.get("text") or "").splitlines()
        print(f"{DIM}     {_clip(lines[0] if lines else '', 100)}{RESET}")


def ask(call, reason):
    """The approver for safe mode: only 'y' or 'yes' means yes."""
    answer = input(f"approve {call['name']}({_clip(str(call['args']), 60)})? [y/N] ")
    return answer.strip().lower() in ("y", "yes")


def main(argv=None):
    """Parse flags, build one Harness, run one task (headless) or a prompt loop."""
    parser = argparse.ArgumentParser(prog="zill", description="Zill: a minimal agentic harness.")
    parser.add_argument("-p", "--prompt", help="run one task headless and exit")
    parser.add_argument("-d", "--workdir", default=".", help="the directory Zill works in (its jail)")
    parser.add_argument("-m", "--model", help="model id (default: ZILL_MODEL or the built-in default)")
    parser.add_argument("--mode", choices=["safe", "yolo", "read-only"], help="permission mode")
    parser.add_argument("--resume", action="store_true", help="continue the latest session")
    parser.add_argument("--max-turns", type=int, default=80)
    args = parser.parse_args(argv)
    headless = args.prompt is not None
    if headless:  # BEFORE the harness exists: yolo is only allowed with bash off the local backend
        os.environ.setdefault("ZILL_BACKEND", "sandbox")
    mode = args.mode or ("yolo" if headless else "safe")
    policy = Policy(mode, approver=None if headless else ask)
    harness = Harness(args.workdir, args.model, policy, on_event=show, max_turns=args.max_turns)
    if args.resume:
        print("resumed the latest session" if harness.resume() else "no session to resume; starting fresh")
    if headless:
        harness.run(args.prompt)
        return 0
    if os.name == "nt":
        os.system("")  # lets the Windows console understand the dim colour codes
    print(f"zill | model {harness.model} | mode {mode} | jail {harness.workdir}\nCtrl+D to quit.")
    while True:
        try:
            task = input("> ")
            if task.strip():
                harness.run(task)
        except EOFError:
            print()
            return 0
        except KeyboardInterrupt:
            print("\nInterrupted. The session log is safe on disk; start with --resume to continue it.")
