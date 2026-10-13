"""Concept: WHAT a tool does. Where it runs is Operations' business, not ours.

Design rules: (1) this file never imports subprocess and never opens a file;
every file or command operation goes through the tool's Operations object, so
the same six tools work on local, sandbox and ssh. (2) Every path is resolved
(jailed) before use. (3) Failures become result strings the model can read and
act on; the loop must never crash because of a tool.
Simplifications: every parameter is a string; the model parses nothing for us.
"""
import fnmatch
import inspect
import posixpath
import re
from dataclasses import dataclass
from typing import Callable

from zill.operations import TOOL_NAMES, LocalOps

MAX_LINES, MAX_OUTPUT, MAX_LISTED, MAX_HITS = 4000, 12000, 500, 200


@dataclass
class Tool:
    """A capability the model can call: a schema (spec) plus the function (run)."""

    name: str
    spec: dict
    run: Callable
    backend: str = "local"


def tool(description, **params):
    """Turn a plain function into a Tool; params maps argument name to its description.

    Arguments without defaults are 'required'; everything is typed string on purpose.
    """
    def wrap(fn):
        args = inspect.signature(fn).parameters
        schema = {"name": fn.__name__, "description": description,
                  "parameters": {"type": "object",
                                 "properties": {a: {"type": "string", "description": params.get(a, a)} for a in args},
                                 "required": [a for a, p in args.items() if p.default is p.empty]}}
        return Tool(fn.__name__, {"schema": schema}, fn)
    return wrap


def _matches(path, pattern):
    """Glob against the whole relative path or its basename; '**/x' also matches a top-level x."""
    names = [pattern] + ([pattern[3:]] if pattern.startswith("**/") else [])
    return any(fnmatch.fnmatch(t, n) for n in names for t in (path, posixpath.basename(path)))


def core_tools(workdir, ops=None):
    """Build the six core tools. ops: one Operations, or a dict tool name -> Operations."""
    ops = ops or LocalOps(workdir)
    by_tool = ops if isinstance(ops, dict) else dict.fromkeys(TOOL_NAMES, ops)

    @tool("Read a text file. Returns its lines numbered 'N<TAB>line'; long files are truncated with a note.",
          path="File path relative to the working directory")
    def read_file(path):
        o = by_tool["read_file"]
        lines = o.read_text(o.resolve(path)).splitlines()
        out = [f"{n}\t{line}" for n, line in enumerate(lines[:MAX_LINES], 1)]
        if len(lines) > MAX_LINES:
            out.append(f"... truncated: file has {len(lines)} lines in total")
        return "\n".join(out)

    @tool("Create or overwrite a file with the given content; parent directories are created.",
          path="File path relative to the working directory", content="Full text of the file")
    def write_file(path, content):
        o = by_tool["write_file"]
        o.write_text(o.resolve(path), content)
        return f"Wrote {len(content)} chars to {path}"

    @tool("Replace one snippet in a file. The old snippet must appear exactly once; copy it exactly.",
          path="File path relative to the working directory", old="Exact text to replace", new="Replacement text")
    def edit_file(path, old, new):
        o = by_tool["edit_file"]
        full = o.resolve(path)
        text = o.read_text(full)
        count = text.count(old)  # the uniqueness rule: an ambiguous edit is refused, never guessed
        if count == 0:
            return "ERROR: snippet not found — read the file and copy it exactly"
        if count > 1:
            return f"ERROR: snippet appears {count} times — include more context to make it unique"
        o.write_text(full, text.replace(old, new, 1))
        return f"Edited {path}"

    @tool("Run a shell command in the working directory. Returns combined output (long output is cut in the middle).",
          command="Shell command to run", timeout="Seconds before the command is stopped (default 120)")
    def bash(command, timeout="120"):
        seconds = int(float(timeout))
        try:
            out, code = by_tool["bash"].run(command, seconds)
        except TimeoutError:
            return f"ERROR: timed out after {seconds}s"
        if len(out) > MAX_OUTPUT:
            out = out[:MAX_OUTPUT // 2] + "\n... truncated ...\n" + out[-MAX_OUTPUT // 2:]
        return out or f"(exit {code}, no output)"

    @tool("List files in the project, sorted. The glob matches the relative path or the file name.",
          pattern="Glob such as '*.py' or 'src/**/*.js' (default: every file)")
    def list_files(pattern="**/*"):
        hits = [p for p in by_tool["list_files"].walk() if _matches(p, pattern)]
        if not hits:
            return "(no files)"
        if len(hits) > MAX_LISTED:
            hits = hits[:MAX_LISTED] + [f"... and {len(hits) - MAX_LISTED} more"]
        return "\n".join(hits)

    @tool("Search file contents with a regular expression. Returns 'path:lineno: text' lines.",
          regex="Python regular expression", pattern="Glob limiting which files are searched (default '*')")
    def grep(regex, pattern="*"):
        try:
            rx = re.compile(regex)
        except re.error as e:
            return f"ERROR: invalid regex: {e}"
        o, hits = by_tool["grep"], []
        for path in (p for p in o.walk() if _matches(p, pattern)):
            try:
                text = o.read_text(o.resolve(path))
            except UnicodeDecodeError:
                continue  # binary file: skip it
            for n, line in enumerate(text.splitlines(), 1):
                if rx.search(line):
                    hits.append(f"{path}:{n}: {line[:200]}")
                    if len(hits) >= MAX_HITS:
                        return "\n".join(hits) + f"\n... stopped at {MAX_HITS} hits"
        return "\n".join(hits) or "(no matches)"

    tools = [read_file, write_file, edit_file, bash, list_files, grep]
    for t in tools:
        t.backend = by_tool[t.name].name
    return tools
