"""Concept: expertise as folders, loaded on demand.

Each skill is skills/<name>/SKILL.md. Only a one-line catalog lives in the system
prompt (cheap); the full text enters the tape only when the model calls use_skill.
Design rules: (1) skills are plain markdown a human can write; (2) progressive
disclosure: catalog always, body only when needed; (3) a bad name is an error
string listing what exists, never an exception.
Simplification: front matter is read line by line, only for 'description:'.
"""
import os

from zill.tools import tool

SKILLS_DIR = "skills"


def _describe(text):
    """The front-matter 'description:' line, else the first non-empty body line."""
    lines = text.splitlines()
    body = lines
    if lines and lines[0].strip() == "---":
        end = next((i for i, line in enumerate(lines[1:], 1) if line.strip() == "---"), None)
        if end:
            for line in lines[1:end]:
                if line.lower().startswith("description:"):
                    return line.split(":", 1)[1].strip().strip("\"'")
            body = lines[end + 1:]
    return next((line.strip() for line in body if line.strip()), "")


def catalog(workdir):
    """Return {name: {"description", "path"}} for every skills/<name>/SKILL.md, sorted by name."""
    root = os.path.join(workdir, SKILLS_DIR)
    found = {}
    for name in sorted(os.listdir(root)) if os.path.isdir(root) else []:
        path = os.path.join(root, name, "SKILL.md")
        if os.path.isfile(path):
            with open(path, encoding="utf-8") as f:
                found[name] = {"description": _describe(f.read()), "path": path}
    return found


def catalog_prompt(workdir):
    """The one-line-per-skill catalog for the system prompt ('' when there are no skills)."""
    skills = catalog(workdir)
    if not skills:
        return ""
    lines = [f"- {name}: {info['description']}" for name, info in skills.items()]
    return "Skills available (load one with the use_skill tool when relevant):\n" + "\n".join(lines)


def read_skill(workdir, name):
    """Return the full SKILL.md text, or an ERROR string listing the available skills."""
    skills = catalog(workdir)
    if name not in skills:
        return f"ERROR: no skill named {name}. Available: {', '.join(skills)}"
    with open(skills[name]["path"], encoding="utf-8") as f:
        return f.read()


def use_skill_tool(workdir):
    """Build the use_skill tool bound to one working directory."""
    @tool("Load the full instructions of a skill from the catalog in your system prompt.",
          name="Skill name exactly as listed in the catalog")
    def use_skill(name):
        return read_skill(workdir, name)

    return use_skill
