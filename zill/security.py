"""Concept: the permission gate. It decides WHAT may run; the path jail in
operations.py decides WHERE it can reach. Two layers, never merged.

Design rules: (1) fail closed: no approver, a crashing approver, or an unknown
mode means "no". (2) Deny rules beat every mode, including yolo. (3) Policy.check
is a before_tool socket: None allows, a reason string blocks.
Simplification: deny patterns are regexes over the command text, a tripwire for
accidents and obvious mistakes, not a parser; the sandbox is the real defence.
"""
import os
import re

READ_TOOLS = {"read_file", "list_files", "grep"}
MODES = ("read-only", "safe", "yolo")
_RM_TARGET = r"(?:/|~|\$HOME)/?\*?(?:\s|$)"  # a whole-system target: /, ~, $HOME (but not build/)
DENY_PATTERNS = [
    r"\brm\s(?=(?:[^;&|]*\s)?-\w*[rR])(?=(?:[^;&|]*\s)?-\w*f)[^;&|]*\s" + _RM_TARGET,  # rm -rf / (any flag order)
    r"\bsudo\b",
    r"\bmkfs\b",
    r"\bdd\s+if=",
    r"\bcurl\b[^|]*\|\s*(?:ba)?sh\b",  # curl ... | sh
    r"\bgit\s+push\b.*--force\b",
    r">\s*/dev/sd",  # redirect onto a disk device
]


class Policy:
    """Decides whether a tool call may run, depending on the mode.

    read-only: only read tools. safe: anything else needs the approver's yes.
    yolo: everything runs, but bash must not be on the local backend.
    """

    def __init__(self, mode="safe", approver=None, backends=None):
        if mode not in MODES:
            raise ValueError(f"unknown mode {mode!r}; valid modes: {', '.join(MODES)}")
        self.mode = mode
        self.approver = approver or (lambda call, reason: False)  # default refuses: fail closed
        self.backends = backends or {}  # tool name -> backend name

    def check(self, call):
        """Return None to allow the call, or a reason string to block it. Order matters."""
        name = call["name"]
        if name == "bash":
            command = str(call.get("args", {}).get("command", ""))
            for pattern in DENY_PATTERNS:
                if re.search(pattern, command, re.IGNORECASE):
                    return f"command matches deny rule: {pattern}"
        if name in READ_TOOLS:
            return None
        if self.mode == "yolo":
            local = self.backends.get("bash", "local") == "local"
            if name == "bash" and local and os.environ.get("ZILL_ALLOW_LOCAL_YOLO") != "1":
                return "yolo + bash needs the sandbox or ssh backend (set ZILL_BACKEND=sandbox)"
            return None
        if self.mode == "read-only":
            return "policy is read-only; only read/list/grep tools are allowed"
        try:
            approved = self.approver(call, f"{name} changes things; approve?") is True
        except Exception:
            approved = False  # a broken approver must never mean yes
        return None if approved else "not approved by user"
