import sys
sys.stdout.reconfigure(encoding="utf-8")
path = "C:/Users/1/Desktop/project/docker/core/admin_core/web/pages_lists.py"
with open(path, encoding="utf-8") as f:
    lines = f.readlines()
start = None
for i in range(len(lines)):
    if 'if may_add:' in lines[i] and 455 < i < 470:
        start = i
        break
if start is None:
    print("Could not find add_form block in page_masters")
    sys.exit(1)
end = start
for j in range(start, min(start + 20, len(lines))):
    if '</form>' in lines[j] and 'add_form' not in lines[j]:
        end = j
        break
print(f"Replacing lines {start+1} to {end+1}")
new_block = [
    '    if may_add:\n',
    '        add_form = (\n',
    "            f'<input type=\"checkbox\" id=\"add-master\" class=\"modal-toggle\">' +\n",
    "            f'<label class=\"btn\" for=\"add-master\" style=\"margin-top:14px\">Добавить мастера</label>' +\n",
    "            '<div class=\"modal\">' +\n",
    "            f'<label class=\"modal-veil\" for=\"add-master\"></label>' +\n",
    "            '<form method=\"post\" action=\"/actions\" class=\"modal-card\">' +\n",
    "            '<div class=\"modal-head\"><b>Новый мастер</b>' +\n",
    "            f'<label class=\"modal-x\" for=\"add-master\">✕</label></div>' +\n",
    "            '<div class=\"modal-body stack\">' +\n",
    "                '<input type=\"hidden\" name=\"action\" value=\"master_create\">' +\n",
    "                f'<input type=\"hidden\" name=\"back\" value=\"{back}\">' +\n",
    '                "{csrf_field(csrf)}" +\n',
    "                '<label class=\"field\"><span>Имя мастера</span>' +\n",
    "                '<input type=\"text\" name=\"name\" placeholder=\"Имя\" required></label>' +\n",
    "            '</div>' +\n",
    "            '<div class=\"modal-foot\">' +\n",
    "                f'<label class=\"btn ghost\" for=\"add-master\">Отмена</label>' +\n",
    "                '<button type=\"submit\">Добавить</button></div>' +\n",
    "            '</form></div>'\n",
    '        )\n',
]
lines[start:end+1] = new_block
with open(path, "w", encoding="utf-8") as f:
    f.writelines(lines)
print("Done - page_masters add form converted to modal")
