#!/usr/bin/env python3
"""Extract hh.ru OAuth app-authorization docs and /token contract."""
import re

t = open("project/hh_spec.json", encoding="utf-8").read()
out = []
def p(*a): out.append(" ".join(str(x) for x in a))

# Section 'Авторизация приложения'
for anchor in ["Авторизация приложения", "authorization-prilozheniya",
               "Avtorizaciya-prilozheniya"]:
    i = t.find(anchor)
    if i >= 0:
        p("=== FOUND ANCHOR:", anchor, "===")
        p(t[i:i+3500].replace("\n\n", "\n"))
        break

# /token endpoint (POST)
for pm in re.finditer(r"^  (/token[^\n:]*):", t, re.M):
    p("\n=== TOKEN PATH:", pm.group(1), "===")
    start = pm.end()
    nxt = re.search(r"^  /[^ \n]", t[start:], re.M)
    block = t[start:start + (nxt.start() if nxt else 5000)]
    p(block[:4500])

open("project/hh_app_auth.txt", "w", encoding="utf-8").write("\n".join(out))
print("done")
