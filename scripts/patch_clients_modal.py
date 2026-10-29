import sys
sys.stdout.reconfigure(encoding="utf-8")
path = "C:/Users/1/Desktop/project/docker/core/admin_core/web/pages_lists.py"
with open(path, encoding="utf-8") as f:
    lines = f.readlines()
start = None
for i in range(len(lines)):
    if 'if may_add:' in lines[i] and 230 < i < 240:
        start = i
        break
if start is None:
    print("Could not find add_form block in page_clients")
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
    "            f'<input type=\"checkbox\" id=\"add-client\" class=\"modal-toggle\">' +\n",
    "            f'<label class=\"btn\" for=\"add-client\" style=\"margin-top:14px\">Добавить клиента</label>' +\n",
    "            '<div class=\"modal\">' +\n",
    "            f'<label class=\"modal-veil\" for=\"add-client\"></label>' +\n",
    "            '<form method=\"post\" action=\"/actions\" class=\"modal-card\">' +\n",
    "            '<div class=\"modal-head\"><b>Новый клиент</b>' +\n",
    "            f'<label class=\"modal-x\" for=\"add-client\">✕</label></div>' +\n",
    "            '<div class=\"modal-body stack\">' +\n",
    "                '<input type=\"hidden\" name=\"action\" value=\"client_create\">' +\n",
    "                f'<input type=\"hidden\" name=\"back\" value=\"{back}\">' +\n",
    '                "{csrf_field(csrf)}" +\n',
    "                '<label class=\"field\"><span>Телефон</span>' +\n",
    "                '<input type=\"tel\" name=\"phone\" placeholder=\"+79001234567\" required></label>' +\n",
    "                '<label class=\"field\"><span>Имя</span>' +\n",
    "                '<input type=\"text\" name=\"name\" placeholder=\"Как обращаться\"></label>' +\n",
    "                '<label class=\"field\"><span>Заметки</span>' +\n",
    "                '<input type=\"text\" name=\"notes\" placeholder=\"аллергии, пожелания\"></label>' +\n",
    "            '</div>' +\n",
    "            '<div class=\"modal-foot\">' +\n",
    "                f'<label class=\"btn ghost\" for=\"add-client\">Отмена</label>' +\n",
    "                '<button type=\"submit\">Добавить</button></div>' +\n",
    "            '</form></div>'\n",
    '        )\n',
]
lines[start:end+1] = new_block
with open(path, "w", encoding="utf-8") as f:
    f.writelines(lines)
print("Done - page_clients add form converted to modal")
