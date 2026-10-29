import sys
sys.stdout.reconfigure(encoding="utf-8")
path = "C:/Users/1/Desktop/project/docker/core/admin_core/web/pages_lists.py"
with open(path, encoding="utf-8") as f:
    lines = f.readlines()
start = None
for i in range(len(lines)):
    if 'if may_add:' in lines[i] and 320 < i < 335:
        start = i
        break
if start is None:
    print("Could not find add_form block in page_services")
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
    "            f'<input type=\"checkbox\" id=\"add-service\" class=\"modal-toggle\">' +\n",
    "            f'<label class=\"btn\" for=\"add-service\" style=\"margin-top:14px\">Добавить услугу</label>' +\n",
    "            '<div class=\"modal\">' +\n",
    "            f'<label class=\"modal-veil\" for=\"add-service\"></label>' +\n",
    "            '<form method=\"post\" action=\"/actions\" class=\"modal-card\">' +\n",
    "            '<div class=\"modal-head\"><b>Новая услуга</b>' +\n",
    "            f'<label class=\"modal-x\" for=\"add-service\">✕</label></div>' +\n",
    "            '<div class=\"modal-body stack\">' +\n",
    "                '<input type=\"hidden\" name=\"action\" value=\"service_create\">' +\n",
    "                f'<input type=\"hidden\" name=\"back\" value=\"{back}\">' +\n",
    '                "{csrf_field(csrf)}" +\n',
    "                '<label class=\"field\"><span>Название услуги</span>' +\n",
    "                '<input type=\"text\" name=\"title\" placeholder=\"название\" required></label>' +\n",
    "                '<label class=\"field\"><span>Цена, ₽</span>' +\n",
    "                '<input type=\"number\" name=\"price\" min=\"0\" step=\"50\" required></label>' +\n",
    "                '<label class=\"field\"><span>Длительность, мин</span>' +\n",
    "                '<input type=\"number\" name=\"duration_min\" value=\"60\" min=\"5\" max=\"600\" step=\"5\" required></label>' +\n",
    "            '</div>' +\n",
    "            '<div class=\"modal-foot\">' +\n",
    "                f'<label class=\"btn ghost\" for=\"add-service\">Отмена</label>' +\n",
    "                '<button type=\"submit\">Добавить</button></div>' +\n",
    "            '</form></div>'\n",
    '        )\n',
]
lines[start:end+1] = new_block
with open(path, "w", encoding="utf-8") as f:
    f.writelines(lines)
print("Done - page_services add form converted to modal")

