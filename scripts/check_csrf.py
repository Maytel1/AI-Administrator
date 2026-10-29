import sys
sys.stdout.reconfigure(encoding="utf-8")
path = "C:/Users/1/Desktop/project/docker/core/admin_core/web/pages_lists.py"
with open(path, encoding="utf-8") as f:
    content = f.read()
idx = 0
remaining = []
while True:
    idx = content.find("{csrf_field(csrf)}", idx)
    if idx == -1:
        break
    start = max(0, idx - 3)
    context = content[start:idx + 25]
    is_fstring = context.startswith('f"') or context.startswith("f'")
    if not is_fstring:
        remaining.append((idx, context))
    idx += 1
print(f"Remaining non-f-string occurrences: {len(remaining)}")
for idx, ctx in remaining:
    print(f"  {idx}: ...{ctx}...")
