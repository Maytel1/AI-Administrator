#!/usr/bin/env python3
"""Extract hh.ru auth docs and vacancy-search endpoints from YAML spec."""
import re

t = open("project/hh_spec.json", encoding="utf-8").read()
out = []
def p(*a): out.append(" ".join(str(x) for x in a))

# Full info.description (general info + auth documentation)
m = re.search(r"^info:\n((?:.|\n)*?)^servers:", t, re.M)
if not m:
    m = re.search(r"^info:\n((?:.|\n)*?)^(security|paths|components):", t, re.M)
if m:
    p("=== INFO/DESCRIPTION ===")
    p(m.group(1)[:6000])

# List all paths containing 'vacanc'
p("\n=== VACANCY PATHS ===")
for pm in re.finditer(r"^  (/vacancies[^\n:]*):", t, re.M):
    p("PATH:", pm.group(1))
    # capture indented block up to next 2-space path
    start = pm.end()
    nxt = re.search(r"^  /[^ \n]", t[start:], re.M)
    block = t[start:start + (nxt.start() if nxt else 4000)]
    methods = re.findall(r"^    (get|post|put|delete|patch):", block, re.M)
    p("   methods:", methods)

# Parameters for the search vacancy GET endpoint
idx = t.find("/vacancies")
seg = t[idx:idx+9000]
p("\n=== /vacancies SEGMENT (params) ===")
for name in ["text", "area", "per_page", "page", "experience", "schedule",
             "employment", "salary", "date_from", "date_to", "professional_role"]:
    if re.search(r"name: %s\b" % name, seg):
        p("has param:", name)

open("project/hh_auth_doc.txt", "w", encoding="utf-8").write("\n".join(out))
print("done")