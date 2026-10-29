import sys
sys.stdout.reconfigure(encoding="utf-8")
path = "C:/Users/1/Desktop/project/docker/core/admin_core/web/pages_core.py"
with open(path, encoding="utf-8") as f:
    lines = f.readlines()
# Replace only the page_today occurrence (line 271, index 270)
# It's the one followed by money(price) on next line, not money(price) + "Источник"
for i in range(len(lines)):
    if 'master_name' in lines[i] and 'service_title' in lines[i]:
        # Check next line - page_today has money(price)</span>' on next line
        if i + 1 < len(lines) and 'money(b["price"])' in lines[i+1] and 'Источник' not in lines[i+1]:
            old = lines[i]
            new = old.replace(
                '{esc(b["master_name"])}</span>',
                '<span class="avatar">{_initials(b["master_name"])}</span> {esc(b["master_name"])}</span>'
            )
            lines[i] = new
            print(f"Replaced line {i+1}")
            break
with open(path, "w", encoding="utf-8") as f:
    f.writelines(lines)
print("Done")
