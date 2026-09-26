# -*- coding: utf-8 -*-
"""
Генератор HTML-версий новых документов проекта (TARIFFS, LIABILITY_OPTIONS,
SALON_WITHOUT_CRM, FAILOVER_SCENARIOS, PDN_LLM_OPTIONS, CONTRACT_PDN_TEMPLATE,
OPEN_QUESTIONS, TEXT_BOT, VOICE_BOT).

Читает Markdown, собирает самодостаточную HTML-страницу в стиле существующих
документов (DEVELOPMENT_PLAN.html / COMPETITORS_TYUMEN.html) и прогоняет
валидацию: баланс тегов, якоря оглавления, число таблиц, остатки разметки.

Запуск:  python build_html_docs.py
Выход:   *.html рядом с *.md для каждого файла из DOCS (перезаписывает).
"""

import datetime
import pathlib
import re
import sys
from html.parser import HTMLParser

sys.stdout.reconfigure(encoding="utf-8")

ROOT = pathlib.Path(__file__).resolve().parent

# md -> (kicker, заголовок H1, lead, цвет точки статуса, текст статуса)
DOCS = {
    "DEVELOPMENT_PLAN.md": (
        "Development Plan · Тюмень",
        "План разработки: единый AI-администратор для автосервисов и стоматологий",
        "Голос 24/7 + мессенджеры (WhatsApp/Telegram) для автосервисов и стоматологий "
        "Тюмени: запись в YCLIENTS/Altegio, AMO CRM, I-dent. Версия 2.0 (26.09.2026) — "
        "LLM ядро на YandexGPT 5.1, целевой стек Yandex Cloud, экономика на клиента, "
        "ROI первых клиентов, скрипты продаж, юр. рамка и дорожная карта 30 дней.",
        "ok", "Версия 2.1 · 26.09.2026",
    ),
    "TARIFFS.md": (
        "Ценообразование",
        "Тарифы — на согласование",
        "Три варианта тарифной сетки и общие условия покупки (setup, триал 7 дней, "
        "рефералка). Итоговый выбор фиксируется в DEVELOPMENT_PLAN.md (фаза 4, биллинг).",
        "warn", "Ждёт выбора владельца",
    ),
    "LIABILITY_OPTIONS.md": (
        "Договор",
        "Ответственность за ошибки бота — варианты на выбор",
        "Четыре формулировки для договора плюс сервисные меры (SLA, журнал аудита). "
        "После выбора вариант фиксируется в CONTRACT_PDN_TEMPLATE.md (раздел 12) и оферте.",
        "warn", "Ждёт выбора владельца",
    ),
    "SALON_WITHOUT_CRM.md": (
        "Продукт",
        "Салон без YCLIENTS / Altegio — сценарии работы",
        "Пять сценариев для микросалонов с тетрадкой и WhatsApp: от внутреннего "
        "календаря продукта до онбординга в Altegio. Выбор определяет фазу 4 и setup.",
        "warn", "Ждёт выбора владельца",
    ),
    "FAILOVER_SCENARIOS.md": (
        "Надёжность",
        "Сбои и деградация — сценарии",
        "Десять сценариев: что видит клиент, как отвечает система, что делает человек "
        "и что настроить заранее. Принцип: наш сбой не должен увеличивать потери салона.",
        "mut", "Вернуться перед фазой 3",
    ),
    "PDN_LLM_OPTIONS.md": (
        "Право и безопасность",
        "Персональные данные и LLM (152-ФЗ) — варианты решений",
        "Юридическая рамка трансграничной передачи и пять путей её избежать: "
        "обезличивание, YandexGPT, DeepSeek, локальная модель, гибрид.",
        "warn", "Ждёт выбора владельца",
    ),
    "CONTRACT_PDN_TEMPLATE.md": (
        "Юридический шаблон",
        "Договор поручения на обработку ПДн — шаблон",
        "Четырнадцать разделов и приложение: роли сторон, состав данных, меры защиты, "
        "доступ салона к записям звонков, экспорт при расторжении.",
        "warn", "Проверить у юриста",
    ),
    "OPEN_QUESTIONS.md": (
        "Парк вопросов",
        "Открытые вопросы — «парк»",
        "Тринадцать отложенных вопросов: бренд, WhatsApp-миграция, TAM, патент, "
        "срок хранения аудио и другие. Возвращаемся к списку в начале каждой фазы.",
        "mut", "Парк — регулярный просмотр",
    ),
    "PLAN.md": (
        "Планирование",
        "План проекта: «Виртуальный администратор»",
        "Приоритетная сфера (салоны/барбершопы Тюмени), формат продукта, источники "
        "данных и custdev по Mom Test; дополнения от 20.09.2026.",
        "ok", "Рабочий план",
    ),
    "IDEAS.md": (
        "Концепция и исследование",
        "Идеи и концепция продукта",
        "Спецификация голосового AI-администратора, регистрация скиллов, исследование "
        "ниши администраторов (Роструд, Тюмень) и принятые решения, включая 20.09.2026.",
        "ok", "Концепция принята",
    ),
    "TEXT_BOT.md": (
        "Текстовый контур · оценки",
        "Текстовый бот (Telegram / WhatsApp / SMS) — оценки и расчёты",
        "Себестоимость по статьям и формулам (токены → ₽, фиксированная инфраструктура → "
        "доля точки), 20 человеко-дней до конца фазы 2, тарифы 5 000–8 000 ₽/мес, маржа 51–66%, "
        "ROI точки 12–31×, метрики приёмки и план недель 5–7. Спутник документа VOICE_BOT.md.",
        "ok", "Работает · осталось 20 дней",
    ),
    "VOICE_BOT.md": (
        "Голосовой контур · оценки",
        "Голосовой бот — оценки и расчёты",
        "SpeechKit v3 + Pipecat + SIP: бюджет латентности < 1,2 с, 1 800–2 016 минут в месяц на "
        "точку, себестоимость 8 985–12 488 ₽/мес, тарифы 25 000–45 000 ₽/мес, маржа 70–75%, "
        "ROI 4,4–5,3×, 36 человеко-дней, failover с порогами, записи разговоров и 152-ФЗ.",
        "warn", "Фаза 3 · скелет готов",
    ),
}

def esc(s: str) -> str:
    """Экранирование спецсимволов HTML (< > &)."""
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def inline(s: str) -> str:
    """Инлайн-разметка Markdown: `code`, **bold**, *italic*."""
    s = esc(s)
    s = re.sub(r"`([^`]+)`", r"<code>\1</code>", s)
    s = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", s)
    s = re.sub(r"(?<![\w*])\*([^*\n]+?)\*(?![\w*])", r"<em>\1</em>", s)
    return s


SEP_CELL = re.compile(r":?-{3,}:?$")


def is_sep_row(cells) -> bool:
    return bool(cells) and all(SEP_CELL.fullmatch(c) for c in cells)


def build_table(rows) -> str:
    """Markdown-таблица -> .tbl-wrap > table (thead при наличии строки-разделителя)."""
    cells_rows = [[c.strip() for c in r.strip().strip("|").split("|")] for r in rows]
    ncols = max(len(r) for r in cells_rows)
    has_sep = len(cells_rows) > 1 and is_sep_row(cells_rows[1])
    head, body_src = (cells_rows[0], cells_rows[2:]) if has_sep else (None, cells_rows)
    wide = " wide" if ncols >= 5 else ""
    thead = ""
    if head:
        head = head + [""] * (ncols - len(head))
        thead = "<thead><tr>" + "".join(f"<th>{inline(c)}</th>" for c in head) + "</tr></thead>"
    body = ""
    for r in body_src:
        r = r + [""] * (ncols - len(r))
        body += "<tr>" + "".join(f"<td>{inline(c)}</td>" for c in r) + "</tr>"
    return f'<div class="tbl-wrap"><table class="tbl{wide}">{thead}<tbody>{body}</tbody></table></div>'


def h2_extra_tag(raw: str) -> str:
    """Чип статуса в заголовке h2 по смыслу секции."""
    if raw.startswith("Рекоменд"):
        return '<span class="tag t-ok">рекомендация</span>'
    if raw.startswith("Открытые пункты"):
        return '<span class="tag t-warn">открытые пункты</span>'
    if raw.startswith("Что решаем позже"):
        return '<span class="tag t-mut">отложено</span>'
    return ""


HEADING = re.compile(r"^(#{1,4})\s+(.+)$")
UL_ITEM = re.compile(r"^[-*]\s+")
OL_ITEM = re.compile(r"^\d+[.)]\s+")
CHECK = re.compile(r"^-\s+\[([ xX])\]\s+(.*)$")
HR = re.compile(r"-{3,}")
STRUCTURAL = re.compile(r"^(#{1,4}\s|\||>|- |\* |\d+[.)]\s|-{3,})")


def md_to_body(text: str) -> str:
    """Markdown -> HTML-фрагмент body (заголовки, таблицы, списки, цитаты, абзацы)."""
    lines = text.splitlines()
    out, i, n, h2n = [], 0, len(lines), 0
    while i < n:
        s = lines[i].strip()
        if not s:
            i += 1
            continue
        m = HEADING.match(s)
        if m:
            lvl, raw = len(m.group(1)), m.group(2).strip()
            if lvl == 1 and not out:  # # Заголовок в начале файла — дублирует H1 из шапки страницы
                i += 1
                continue
            if lvl == 2:
                mnum = re.match(r"^(\d+)\.\s+(.*)$", raw)
                if mnum:  # номер из Markdown: "## 0. Резюме" -> бейдж «0», якорь #s0
                    h2n = int(mnum.group(1))
                    shown = mnum.group(2).strip()
                else:
                    h2n += 1
                    shown = raw
                out.append(
                    f'<h2 id="s{h2n}"><span class="num">{h2n}</span>'
                    f"<span>{inline(shown)}</span>{h2_extra_tag(raw)}</h2>"
                )
            else:
                out.append(f"<h{lvl}>{inline(raw)}</h{lvl}>")
            i += 1
            continue
        if HR.fullmatch(s):
            out.append("<hr>")
            i += 1
            continue
        if s.startswith(">"):
            buf = []
            while i < n and lines[i].strip().startswith(">"):
                buf.append(inline(lines[i].strip().lstrip(">").strip()))
                i += 1
            out.append('<blockquote class="callout c-warn">' + "<br>".join(buf) + "</blockquote>")
            continue
        if s.startswith("|"):
            rows = []
            while i < n and lines[i].strip().startswith("|"):
                rows.append(lines[i])
                i += 1
            out.append(build_table(rows))
            continue
        if UL_ITEM.match(s) or OL_ITEM.match(s):
            ordered = bool(OL_ITEM.match(s))
            pat = OL_ITEM if ordered else UL_ITEM
            items = []
            while i < n:
                line = lines[i].strip()
                cm = CHECK.match(line)
                if cm and not ordered:
                    mark = "☑" if cm.group(1).lower() == "x" else "☐"
                    items.append(f'<span class="cb">{mark}</span> {inline(cm.group(2))}')
                    i += 1
                    continue
                if not pat.match(line):
                    break
                items.append(inline(pat.sub("", line, count=1)))
                i += 1
            tag = "ol" if ordered else "ul"
            out.append(f"<{tag}>" + "".join(f"<li>{it}</li>" for it in items) + f"</{tag}>")
            continue
        buf = [s]
        i += 1
        while i < n:
            nxt = lines[i].strip()
            if not nxt or STRUCTURAL.match(nxt):
                break
            buf.append(nxt)
            i += 1
        if buf == ["-"]:
            continue  # одинокий маркер пустого пункта (IDEAS.md, раздел «Заметки»)
        cls = ' class="lead-block"' if buf[0].startswith("Статус:") else ""
        out.append(f"<p{cls}>" + "<br>".join(inline(b) for b in buf) + "</p>")
    return "\n".join(out)


CSS = """
:root{--bg:#f4f1ea;--paper:#fffefb;--ink:#1e2733;--muted:#63707f;--line:#e4dfd3;--accent:#0f766e;--accent-ink:#0b5d57;--accent-soft:#e3f1ee;--warn-ink:#93261b;--warn-soft:#fceeea;--ok:#1a7f37;--ok-soft:#e8f4ea;--radius:14px;--shadow:0 1px 2px rgba(30,39,51,.04),0 10px 28px rgba(30,39,51,.07)}
*{box-sizing:border-box}
html{scroll-behavior:smooth}
body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.72 "Inter",system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;-webkit-font-smoothing:antialiased}
::selection{background:var(--accent);color:#fff}
h1,h2,h3{font-family:"Manrope","Inter",sans-serif;line-height:1.28;margin:0}
a{color:var(--accent-ink)}
:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
main{padding:40px 22px 80px}
.doc{max-width:920px;margin:0 auto}
.doc-header{background:var(--paper);border:1px solid var(--line);border-radius:18px;padding:30px 34px 24px;box-shadow:var(--shadow)}
.kicker{font:700 11.5px "Inter",sans-serif;letter-spacing:.16em;text-transform:uppercase;color:var(--accent)}
.doc-header h1{font-size:30px;font-weight:800;letter-spacing:-.01em;margin:10px 0 10px}
.lead{font-size:16.5px;color:var(--muted);margin:0;max-width:75ch}
.chips{display:flex;flex-wrap:wrap;gap:8px;margin-top:16px}
.chip{display:inline-flex;align-items:center;gap:7px;font:600 12.5px "Inter",sans-serif;padding:5px 12px;border-radius:999px;background:var(--paper);border:1px solid var(--line)}
.chip code{font-size:11.5px;padding:1px 6px}
.dot{flex:none;width:8px;height:8px;border-radius:50%;background:#9aa5b1}
.dot.warn{background:#e07856}
.dot.mut{background:#9aa5b1}
.dot.ok{background:#3fae5a}
.rule{width:64px;height:3px;border-radius:3px;background:var(--accent);margin:20px 0 14px}
.toc-chips{display:flex;flex-wrap:wrap;gap:8px}
.toc-chips a{font:600 12.5px "Inter",sans-serif;padding:5px 12px;border-radius:999px;background:var(--accent-soft);color:var(--accent-ink);text-decoration:none}
.toc-chips a:hover{background:#d3e8e2}
h2{display:flex;align-items:center;flex-wrap:wrap;gap:12px;font-size:22.5px;margin:46px 0 14px;scroll-margin-top:14px}
h2 .num{flex:none;width:34px;height:34px;border-radius:10px;background:var(--accent);color:#fff;display:grid;place-items:center;font:800 16px "Manrope",sans-serif}
h3{font-size:16.5px;margin:22px 0 8px}
p{margin:10px 0}
p.lead-block{background:var(--accent-soft);border:1px solid #c3e0da;border-radius:12px;padding:12px 16px;font-size:15px}
.tag{display:inline-block;font:700 11px "Inter",sans-serif;padding:3px 9px;border-radius:999px;letter-spacing:.06em;text-transform:uppercase}
.t-warn{background:#fbe3df;color:var(--warn-ink)}
.t-ok{background:var(--ok-soft);color:var(--ok)}
.t-mut{background:#ece9e1;color:var(--muted)}
blockquote.callout{border:1px solid #f0cdc6;background:var(--warn-soft);border-radius:12px;padding:13px 16px;margin:14px 0;font-size:15px}
.tbl-wrap{overflow-x:auto;border:1px solid var(--line);border-radius:12px;box-shadow:var(--shadow);background:var(--paper);margin:14px 0 8px}
table{width:100%;border-collapse:collapse;font-size:14px}
table.wide{min-width:900px}
th{background:#edf4f1;color:var(--accent-ink);font:600 12.5px "Inter",sans-serif;text-align:left;padding:9px 12px;border:1px solid var(--line);white-space:nowrap}
td{padding:9px 12px;border:1px solid var(--line);vertical-align:top}
tbody tr:nth-child(even) td{background:#faf8f2}
tbody tr:hover td{background:#f3f7f5}
ul,ol{margin:10px 0;padding-left:24px}
li{margin:6px 0}
li::marker{color:var(--accent)}
li .cb{color:var(--accent-ink);font-weight:700}
code{font:500 .92em "JetBrains Mono",Consolas,monospace;background:#eef0f6;padding:2px 6px;border-radius:6px;color:#24304a}
strong{color:#101828}
hr{border:0;border-top:1px solid var(--line);margin:28px 0}
.src{margin-top:46px;padding-top:16px;border-top:1px solid var(--line);font-size:13px;color:var(--muted);font-style:italic}
.src code{font-style:normal;background:#efece4}
#top{position:fixed;right:22px;bottom:22px;width:44px;height:44px;border-radius:50%;border:1px solid var(--line);background:var(--paper);color:var(--ink);font-size:17px;cursor:pointer;box-shadow:var(--shadow);opacity:0;pointer-events:none;transition:opacity .25s;z-index:55}
#top.show{opacity:1;pointer-events:auto}
#top:hover{background:var(--accent-soft)}
@media print{body{background:#fff}main{padding:0}.doc-header{box-shadow:none;border-radius:0}#top{display:none}h2{break-after:avoid}.tbl-wrap{box-shadow:none;overflow:visible}table{font-size:12px}}
"""

JS = """
const top=document.getElementById('top');
addEventListener('scroll',()=>top.classList.toggle('show',scrollY>400));
top.addEventListener('click',()=>scrollTo({top:0,behavior:'smooth'}));
"""


def page_html(kicker, title, lead, dot, pill_text, today, src, toc, body) -> str:
    chips = (
        f'<span class="chip"><span class="dot {dot}"></span>{esc(pill_text)}</span>\n'
        f'<span class="chip">обновлено {esc(today)}</span>\n'
        f'<span class="chip">источник: <code>{esc(src)}</code></span>'
    )
    footer = (
        "<footer class=\"src\">Сгенерировано из <code>" + esc(src)
        + "</code> · перегенерация: <code>python build_html_docs.py</code>"
        " · не редактируйте HTML вручную</footer>\n"
    )
    return (
        "<!DOCTYPE html>\n<html lang=\"ru\">\n<head>\n<meta charset=\"UTF-8\">\n"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n"
        "<meta name=\"color-scheme\" content=\"light\">\n"
        f"<title>{esc(title)}</title>\n"
        "<link rel=\"preconnect\" href=\"https://fonts.googleapis.com\">\n"
        "<link rel=\"preconnect\" href=\"https://fonts.gstatic.com\" crossorigin>\n"
        "<link href=\"https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700"
        "&family=Manrope:wght@700;800&family=JetBrains+Mono:wght@500&display=swap\" rel=\"stylesheet\">\n"
        f"<style>{CSS}</style>\n</head>\n<body>\n<main><article class=\"doc\">\n"
        "<header class=\"doc-header\">\n"
        f"<div class=\"kicker\">{esc(kicker)}</div>\n"
        f"<h1>{esc(title)}</h1>\n<p class=\"lead\">{esc(lead)}</p>\n"
        f"<div class=\"chips\">\n{chips}\n</div>\n<div class=\"rule\"></div>\n"
        f"<nav class=\"toc-chips\">{toc}</nav>\n</header>\n"
        f"{body}\n{footer}"
        "</article></main>\n<button id=\"top\" title=\"Наверх\">↑</button>\n"
        f"<script>{JS}</script>\n</body>\n</html>\n"
    )


class TagBalance(HTMLParser):
    """Проверка парности тегов и якорей (упрощённый аналог validate_plan_html.py)."""

    VOID = {"meta", "link", "br", "input", "img", "hr", "source", "wbr"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack, self.errors, self.ids, self.hrefs = [], [], set(), []

    def handle_starttag(self, tag, attrs):
        d = dict(attrs)
        if "id" in d:
            self.ids.add(d["id"])
        if tag == "a" and d.get("href", "").startswith("#"):
            self.hrefs.append(d["href"][1:])
        if tag not in self.VOID:
            self.stack.append(tag)

    def handle_endtag(self, tag):
        if not self.stack:
            self.errors.append(f"extra closing: {tag}")
            return
        if self.stack[-1] == tag:
            self.stack.pop()
        elif tag in self.stack:
            while self.stack and self.stack[-1] != tag:
                self.errors.append(f"unclosed: {self.stack.pop()}")
            if self.stack:
                self.stack.pop()
        else:
            self.errors.append(f"stray closing: {tag}")


def md_table_count(text: str) -> int:
    count, in_table = 0, False
    for line in text.splitlines():
        if line.strip().startswith("|"):
            if not in_table:
                count += 1
                in_table = True
        else:
            in_table = False
    return count


def validate_page(page: str, md_tables: int, n_h2: int) -> list:
    v = TagBalance()
    v.feed(page)
    v.close()
    errs = list(v.errors)
    errs += [f"never closed: {t}" for t in v.stack]
    broken = [h for h in v.hrefs if h and h not in v.ids]
    if broken:
        errs.append(f"broken anchors: {broken}")
    if "**" in page:
        errs.append("leftover ** markdown")
    if re.search(r"<p[^>]*>\|", page):
        errs.append("table row leaked into <p>")
    if page.count('<div class="tbl-wrap"') != md_tables:
        errs.append(f"tables html {page.count('<div')} != md {md_tables}")
    if page.count("<h2") != n_h2:
        errs.append(f"h2 html {page.count('<h2')} != md {n_h2}")
    return errs


def main() -> None:
    today = datetime.date.today().strftime("%d.%m.%Y")
    all_ok = True
    for md_name, (kicker, title, lead, dot, pill_text) in DOCS.items():
        md_path = ROOT / md_name
        text = md_path.read_text(encoding="utf-8-sig")
        body = md_to_body(text)
        h2_titles = re.findall(r"(?m)^##\s+(.+)$", text)
        toc_items, auto = [], 0
        for name in h2_titles:
            name = name.strip()
            mnum = re.match(r"^(\d+)\.\s+(.*)$", name)
            if mnum:  # сквозная нумерация из Markdown ("## 4. Сколько это стоит")
                auto = int(mnum.group(1))
                toc_items.append((auto, f"{mnum.group(1)}. {mnum.group(2).strip()}"))
            else:
                auto += 1
                toc_items.append((auto, name))
        toc = "".join(f'<a href="#s{i}">{esc(label)}</a>' for i, label in toc_items)
        page = page_html(kicker, title, lead, dot, pill_text, today, md_name, toc, body)
        html_path = md_path.with_suffix(".html")
        html_path.write_text(page, encoding="utf-8")
        errs = validate_page(page, md_table_count(text), len(h2_titles))
        status = "OK" if not errs else "FAIL: " + "; ".join(errs)
        print(f"{md_name:26s} -> {html_path.name:26s} {len(page):7d} chars  {status}")
        all_ok &= not errs
    print("RESULT:", "OK" if all_ok else "FAIL")
    sys.exit(0 if all_ok else 1)


if __name__ == "__main__":
    main()




