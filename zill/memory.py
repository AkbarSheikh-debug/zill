"""Concept: long-term memory and the system prompt, on two clocks.

The tape (the messages list) grows DURING a session; the system prompt is rebuilt
BETWEEN sessions. remember() appends to ZILL.md on disk, and the fact reaches the
prompt only at the next build_system_prompt call, in the next session.
Design rules: (1) memory is a plain file a human can read and edit; (2) append
only, never rewrite; (3) the prompt is assembled from small labelled sections.
Simplification: no deduplication; the model is told what is worth saving.
"""
import os
import platform

from zill.tools import tool

MEMORY_FILE = "ZILL.md"
BASE_PROMPT = """You are Zill, a small, sharp coding agent working inside one directory with the tools provided.

Rules:
- Act, don't narrate.
- Inspect before assuming.
- Prefer edit_file for small changes.
- After building something, verify it by running it or reading it back.
- If a tool call fails, read the error and fix the cause; never repeat a failing call unchanged.
- When the task is complete, reply with a short plain-text summary and stop calling tools."""


def build_system_prompt(workdir, extra=""):
    """Assemble base prompt, environment line, project memory and extra, one blank line apart."""
    parts = [BASE_PROMPT, f"Environment: {platform.system()}, working directory: {os.path.realpath(workdir)}"]
    path = os.path.join(workdir, MEMORY_FILE)
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            parts.append(f"Project memory ({MEMORY_FILE}):\n{f.read().strip()}")
    if extra:
        parts.append(extra)
    return "\n\n".join(parts)


def remember(workdir, note):
    """Append one note to ZILL.md (created if missing); it shows up in the NEXT session's prompt."""
    with open(os.path.join(workdir, MEMORY_FILE), "a", encoding="utf-8") as f:
        f.write(f"- {note.strip()}\n")
    return f"Remembered in {MEMORY_FILE}"


_save = remember  # the inner function below takes the name 'remember', which is the tool's name


def remember_tool(workdir):
    """Build the remember tool bound to one working directory."""
    @tool("Save a durable project fact that a new colleague would need (how to run tests, a convention, "
          "a gotcha). Never save a transcript or a one-off detail.",
          note="One short, self-contained fact")
    def remember(note):
        return _save(workdir, note)

    return remember
