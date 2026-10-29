import sys
sys.stdout.reconfigure(encoding="utf-8")
path = "C:/Users/1/Desktop/project/docker/core/admin_core/web/pages_lists.py"
with open(path, encoding="utf-8") as f:
    lines = f.readlines()
print("Line 1303:", repr(lines[1302]))
# Replace with double-quoted outer string to avoid nested single-quote conflict
lines[1302] = "        list_block = '<div class=\"empty\">Контрагентов пока нет — добавьте первого</div>'\n"
print("Line 1303 new:", repr(lines[1302]))
with open(path, "w", encoding="utf-8") as f:
    f.writelines(lines)
print("Fixed")
