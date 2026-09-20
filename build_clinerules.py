#!/usr/bin/env python3
"""Generate Cline rules that emulate Agent Skills for a non-Anthropic model.

Reads project/skills/skills.json and writes a .clinerules file at the
workspace root (the parent of this project folder) so Cline loads it
automatically at the start of every task.
"""
import os
import json

HERE = os.path.dirname(os.path.abspath(__file__))
SKILLS_JSON = os.path.join(HERE, "skills", "skills.json")
WORKSPACE_ROOT = os.path.dirname(HERE)
RULES_DIR = os.path.join(WORKSPACE_ROOT, ".clinerules")
OUT = os.path.join(RULES_DIR, "skills.md")

HEADER = """# Agent Skills System (Cline + DeepSeek)

This workspace has {count} Agent Skills installed under `project/skills/`.
Agent Skills are a native Anthropic format. DeepSeek does not load them
automatically, so this rule emulates the Agent Skills behaviour for Cline.

## How to use a skill

1. When a task matches a skill's trigger (see the index below), read that
   skill's `SKILL.md` file at the given path.
2. Treat the instructions in that file as authoritative guidance for the task.
3. Skills may reference other skills - load those too when instructed.
4. Prefer the most specific matching skill. If several match, load the most
   relevant one first.

## Loading protocol

- Consult this index before starting any specialized task.
- `product-marketing` is the foundation for all marketing work - load it first.
- For market/competitor/customer analysis use the "Startup and Business" and
  "Marketing" sections below.
- For documents (decks, spreadsheets, Word, PDF) use the "Documents" section.

## Skill index

| Skill | Path | When to use |
|---|---|---|
"""


def main():
    with open(SKILLS_JSON, "r", encoding="utf-8") as f:
        skills = json.load(f)

    lines = [HEADER.format(count=len(skills))]
    current = None
    for s in skills:
        if s["set"] != current:
            current = s["set"]
            lines.append("")
            lines.append("### " + current)
            lines.append("")
            lines.append("| Skill | Path | When to use |")
            lines.append("|---|---|---|")
        desc = (s.get("description") or "").replace("|", "/").replace("\n", " ")
        if len(desc) > 200:
            desc = desc[:197] + "..."
        lines.append("| `%s` | `%s` | %s |" % (s["name"], s["path"], desc))
    lines.append("")

    os.makedirs(RULES_DIR, exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    print("WROTE", OUT)
    print("SKILLS", len(skills))


if __name__ == "__main__":
    main()