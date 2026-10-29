"""Concept: durable sessions. Append is remember.

Every message is written to disk the moment it exists, as one JSON line, never
rewritten. A crash can therefore damage at most the LAST line (the torn tail).
Design rules: (1) append-only, flushed and fsynced per message; (2) loading
tolerates a torn tail by keeping everything before it; (3) loading repairs the
one thing a crash can break: every tool call must get exactly one result.
Simplification: two sessions created in the same second with the same label share a file.
"""
import json
import os
import re
import time

SESSION_DIR = os.path.join(".zill", "sessions")


def new_session(workdir, label="session"):
    """Create the session directory and return a fresh .jsonl path: <timestamp>-<slug>."""
    folder = os.path.join(workdir, SESSION_DIR)
    os.makedirs(folder, exist_ok=True)
    slug = re.sub(r"[^a-z0-9-]+", "-", label.lower()).strip("-")[:40] or "session"
    return os.path.join(folder, f"{int(time.time())}-{slug}.jsonl")


def append(path, message):
    """Write one message as one JSON line and force it to disk before returning."""
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(message, ensure_ascii=False) + "\n")
        f.flush()
        os.fsync(f.fileno())


def latest(workdir):
    """Path of the newest session file by modification time, or None."""
    folder = os.path.join(workdir, SESSION_DIR)
    if not os.path.isdir(folder):
        return None
    files = [os.path.join(folder, n) for n in os.listdir(folder) if n.endswith(".jsonl")]
    return max(files, key=os.path.getmtime) if files else None


def load(path):
    """Read a session file, stopping at a torn tail, and repair open tool calls."""
    messages = []
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            try:
                message = json.loads(line)
            except ValueError:
                break  # torn tail: a crash cut this line; keep everything before it
            if not isinstance(message, dict):
                break
            messages.append(message)
    return _repair(messages)


def _repair(messages):
    """Give every unanswered tool call of the last assistant message an honest 'interrupted' result."""
    last = next((i for i in range(len(messages) - 1, -1, -1) if messages[i]["role"] == "assistant"), None)
    if last is None:
        return messages
    calls = messages[last].get("tool_calls", [])
    answered = sum(1 for m in messages[last + 1:] if m["role"] == "tool")
    # Never pretend a tool ran, and never delete the request: just say it did not finish.
    missing = [{"role": "tool", "name": c["name"], "text": "Interrupted before this ran (process restarted)."}
               for c in calls[answered:]]
    return messages + missing
