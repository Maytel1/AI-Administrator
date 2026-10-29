#!/usr/bin/env python3
"""Validate registered skills: file exists, frontmatter valid, no broken refs."""
import os
import re
import json

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = os.path.join(HERE, "skills")

with open(os.path.join(BASE, "skills.json"), encoding="utf-8") as f:
    skills = json.load(f)

FM = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)
NAME = re.compile(r"^name:\s*(.+?)\s*$", re.MULTILINE)
DESC = re.compile(r"^description:\s*(.+?)\s*$", re.MULTILINE)

ok = missing = no_fm = no_name = no_desc = empty = 0
problems = []

for s in skills:
    path = os.path.join(BASE, s["path"])
    if not os.path.isfile(path):
        missing += 1
        problems.append("MISSING " + s["path"])
        continue
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            text = f.read()
    except OSError:
        missing += 1
        problems.append("UNREADABLE " + s["path"])
        continue
    if len(text.strip()) < 20:
        empty += 1
        problems.append("EMPTY " + s["path"])
        continue
    m = FM.match(text)
    if not m:
        no_fm += 1
        problems.append("NO_FRONTMATTER " + s["path"])
        continue
    block = m.group(1)
    if not NAME.search(block):
        no_name += 1
        problems.append("NO_NAME " + s["path"])
    if not DESC.search(block):
        no_desc += 1
        problems.append("NO_DESC " + s["path"])
    ok += 1

print("TOTAL", len(skills))
print("OK", ok)
print("MISSING", missing)
print("EMPTY", empty)
print("NO_FRONTMATTER", no_fm)
print("NO_NAME", no_name)
print("NO_DESC", no_desc)
print("PROBLEMS", len(problems))
for p in problems[:40]:
    print(p)
