import sys
sys.stdout.reconfigure(encoding="utf-8")
path = "C:/Users/1/Desktop/project/docker/core/admin_core/web/pages_lists.py"
with open(path, encoding="utf-8") as f:
    content = f.read()

old_block = '''    # — Список контрагентов —
    rows = []
    for c in counterparties:
        ctype = CLIENT_TYPES.get(str(c.get("client_type")), str(c.get("client_type")))
        rows.append(
            '<div class="m-card">'
            f'<div class="m-top"><b>{esc(c.get("title"))}</b>'
            + badge(esc(ctype), "") + "</div>"
            f'<span class="meta">ИНН {esc(c.get("inn") or "—")} · '
            f'{esc(c.get("legal_name") or "")}</span>'
            + (
                f'<span class="meta">Договор № {esc(c["contract_no"])}</span>'
                if c.get("contract_no") else ""
            )
            + '<div class="row-end">'
            + f'<a class="tab" href="/counterparties?edit={esc(c["id"])}">'
              "Править</a> "
            + action_form(
                "counterparty_toggle", csrf, "/counterparties",
                f'<input type="hidden" name="counterparty_id" value="{esc(c["id"])}">'
                f'<input type="hidden" name="is_active" value="'
                f'{0 if c.get("is_active") else 1}">',
                "Архивировать" if c.get("is_active") else "Вернуть",
            )
            + "</div></div>"
        )
    list_block = (
        f'<div class="m-grid">{"".join(rows)}</div>'
        if rows else '<div class="empty">Контрагентов пока нет — добавьте первого</div>'
    )'''

new_block = '''    # — Список контрагентов (таблица) —
    trs = []
    for c in counterparties:
        ctype = CLIENT_TYPES.get(str(c.get("client_type")), str(c.get("client_type")))
        status_badge = badge("активен", "green") if c.get("is_active") else badge("в архиве", "gray")
        trs.append(
            '<tr>'
            f'<td><b>{esc(c.get("title"))}</b> {status_badge}</td>'
            f'<td>{esc(ctype)}</td>'
            f'<td>{esc(c.get("inn") or "—")}</td>'
            f'<td>{esc(c.get("legal_name") or "")}</td>'
            f'<td>{esc(c.get("contract_no") or "—")}</td>'
            '<td class="row-end">'
            + f'<a class="tab" href="/counterparties?edit={esc(c["id"])}">Править</a> '
            + action_form(
                "counterparty_toggle", csrf, "/counterparties",
                f'<input type="hidden" name="counterparty_id" value="{esc(c["id"])}">'
                f'<input type="hidden" name="is_active" value="'
                f'{0 if c.get("is_active") else 1}">',
                "Архивировать" if c.get("is_active") else "Вернуть",
            )
            + "</td>"
            + "</tr>"
        )
    if trs:
        thead = (
            '<thead><tr>'
            '<th>Название</th><th>Тип</th><th>ИНН</th>'
            '<th>Наименование в документах</th><th>Договор</th><th>Действия</th>'
            '</tr></thead>'
        )
        tbody = f'<tbody>{"".join(trs)}</tbody>'
        list_block = f'<table>{thead}{tbody}</table>'
    else:
        list_block = '<div class="empty">Контрагентов пока нет — добавьте первого</div>'''

if old_block not in content:
    print("ERROR: old_block not found!")
    # Show what we have around "Список контрагентов"
    idx = content.find('Список контрагентов')
    if idx >= 0:
        print(repr(content[idx:idx+500]))
    sys.exit(1)

content = content.replace(old_block, new_block, 1)
with open(path, "w", encoding="utf-8") as f:
    f.write(content)
print("Done - page_counterparties list converted to table")
