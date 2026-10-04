#!/usr/bin/env python3
"""Remove duplicate agent-specific copies of skills. Use --dry-run to preview."""
import os
import sys
import json
import shutil

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = os.path.join(HERE, "skills")
DRY = "--dry-run" in sys.argv

with open(os.path.join(BASE, "skills.json"), encoding="utf-8") as f:
    skills = json.load(f)
keep = {os.path.normpath(os.path.join(BASE, s["path"])) for s in skills}

to_delete = []
for dp, dn, fn in os.walk(BASE):
    if "SKILL.md" in fn:
        p = os.path.normpath(os.path.join(dp, "SKILL.md"))
        if p not in keep:
            to_delete.append(dp)

to_delete.sort(key=len)
final = []
for d in to_delete:
    if not any(d.startswith(p + os.sep) for p in final):
        final.append(d)

files = size = 0
for d in final:
    for dp, dn, fn in os.walk(d):
        for n in fn:
            try:
                size += os.path.getsize(os.path.join(dp, n))
                files += 1
            except OSError:
                pass

print("DUPLICATE_DIRS", len(final))
print("FILES", files, "MB", round(size / 1048576.0, 2))
for d in final[:30]:
    print("DEL", os.path.relpath(d, BASE))
if DRY:
    print("DRY_RUN")
else:
    for d in final:
        shutil.rmtree(d, ignore_errors=True)
    print("DELETED", len(final))
