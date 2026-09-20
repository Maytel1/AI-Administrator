#!/usr/bin/env python3
"""Build a canonical skill index from the installed Agent Skill sets.

Scans project/skills, reads YAML frontmatter (name/description) from every
SKILL.md, de-duplicates by skill name (skipping hidden agent-specific copies),
and writes project/skills/INDEX.md plus project/skills/skills.json.
"""
import os
import re
import json

BASE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "skills")
OUT_MD = os.path.join(BASE, "INDEX.md")
OUT_JSON = os.path.join(BASE, "skills.json")

# Skill sets and their canonical skill directory (relative to BASE)
SETS = [
    ("Marketing (coreyhaines31)", "marketingskills/skills"),
    ("Startup and Business (wondelai)", "wondelai-skills"),
    ("Documents (Anthropic official)", "anthropic-skills/skills"),
    ("Full library (alirezarezvani)", "alirezarezvani-claude-skills"),
]

# Directories holding duplicate agent-specific copies or non-skill content
EXCLUDE_DIR_PARTS = {
    ".git", ".github", ".claude", ".codex", ".gemini", ".cursor",
    ".windsurf", ".pi", ".hermes", ".agents", ".claude-plugin",
    ".codex-plugin", "node_modules", "plugins",
}

FM_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)
NAME_RE = re.compile(r"^name:\s*(.+?)\s*$", re.MULTILINE)
DESC_RE = re.compile(r"^description:\s*(.+?)\s*$", re.MULTILINE)


def read_frontmatter(path):
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            text = f.read(20000)
    except OSError:
        return None, None
    m = FM_RE.match(text)
    block = m.group(1) if m else text
    name = NAME_RE.search(block)
    desc = DESC_RE.search(block)
    name = name.group(1).strip().strip('"').strip("'") if name else None
    desc = desc.group(1).strip().strip('"').strip("'") if desc else None
    return name, desc


def has_excluded_part(rel):
    parts = rel.replace("\\", "/").split("/")
    return any(p in EXCLUDE_DIR_PARTS for p in parts)


def main():
    skills = {}
    for label, subdir in SETS:
        root = os.path.join(BASE, subdir)
        if not os.path.isdir(root):
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            if "SKILL.md" not in filenames:
                continue
            rel = os.path.relpath(os.path.join(dirpath, "SKILL.md"), BASE)
            if has_excluded_part(rel):
                continue
            name, desc = read_frontmatter(os.path.join(dirpath, "SKILL.md"))
            if not name:
                name = os.path.basename(dirpath)
            key = name.lower()
            if key in skills:
                continue
            skills[key] = {
                "name": name,
                "set": label,
                "path": rel.replace("\\", "/"),
                "description": (desc or "").strip(),
            }

    ordered = sorted(skills.values(), key=lambda s: (s["set"], s["name"]))

    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(ordered, f, ensure_ascii=False, indent=2)

    lines = ["# Skill Index", ""]
    lines.append("Automatically generated index of registered Agent Skills.")
    lines.append("")
    lines.append("To load a skill, read its `path` file and follow its instructions.")
    lines.append("")
    current = None
    for s in ordered:
        if s["set"] != current:
            current = s["set"]
            lines.append("")
            lines.append("## " + current)
            lines.append("")
            lines.append("| Skill | Path | When to use |")
            lines.append("|---|---|---|")
        desc = s["description"].replace("|", "/").replace("\n", " ")
        if len(desc) > 220:
            desc = desc[:217] + "..."
        lines.append("| `%s` | `%s` | %s |" % (s["name"], s["path"], desc))
    lines.append("")

    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    print("TOTAL_SKILLS", len(ordered))
    per_set = {}
    for s in ordered:
        per_set[s["set"]] = per_set.get(s["set"], 0) + 1
    for k, v in per_set.items():
        print("SET", k, v)


if __name__ == "__main__":
    main()