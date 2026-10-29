"""Валидатор DEVELOPMENT_PLAN.html — паритет с DEVELOPMENT_PLAN.md.

Проверяет: баланс тегов, якоря оглавления, совпадение числа секций/таблиц/
чек-пунктов с Markdown-источником, отсутствие остаточной разметки и наличие
ключевых данных v2.0 (YandexGPT, Yandex Cloud, экономика, первые клиенты).

Запуск: python validate_plan_html.py (exit 0 = OK)
"""
import pathlib
import re
import sys
from html.parser import HTMLParser

sys.stdout.reconfigure(encoding="utf-8")

ROOT = pathlib.Path(__file__).resolve().parent

class V(HTMLParser):
    VOID = {'meta', 'link', 'br', 'input', 'img', 'hr', 'source', 'wbr'}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack = []
        self.errors = []
        self.ids = set()
        self.hrefs = []
        self.checkboxes = 0
        self.labels = 0

    def handle_starttag(self, tag, attrs):
        d = dict(attrs)
        if 'id' in d:
            self.ids.add(d['id'])
        if tag == 'a' and d.get('href', '').startswith('#'):
            self.hrefs.append(d['href'][1:])
        if tag == 'input' and d.get('type') == 'checkbox':
            self.checkboxes += 1
        if tag == 'label' and 'chk' in (d.get('class') or ''):
            self.labels += 1
        if tag not in self.VOID:
            self.stack.append(tag)

    def handle_endtag(self, tag):
        if not self.stack:
            self.errors.append('extra closing: ' + tag)
            return
        if self.stack[-1] == tag:
            self.stack.pop()
        elif tag in self.stack:
            while self.stack and self.stack[-1] != tag:
                self.errors.append('unclosed: ' + self.stack.pop())
            if self.stack:
                self.stack.pop()
        else:
            self.errors.append('stray closing: ' + tag)


MD = ROOT / "DEVELOPMENT_PLAN.md"
HTML = ROOT / "DEVELOPMENT_PLAN.html"


def md_table_count(text: str) -> int:
    """Число Markdown-таблиц (блоков подряд идущих строк, начинающихся с '|')."""
    count, in_table = 0, False
    for line in text.splitlines():
        if line.strip().startswith("|"):
            if not in_table:
                count += 1
                in_table = True
        else:
            in_table = False
    return count


md = MD.read_text(encoding="utf-8-sig")
src = HTML.read_text(encoding="utf-8")

p = V()
p.feed(src)
p.close()
never_closed = [t for t in p.stack if t not in ("html", "body")]
toc_hrefs = [h for h in p.hrefs if h]

# --- ожидаемые значения берём из Markdown-источника ---
md_h2 = re.findall(r"(?m)^##\s+(\d+)\.", md)
md_tables = md_table_count(md)
md_checks = len(re.findall(r"(?m)^-\s+\[[ xX]\]", md))
need_ids = ["s" + n for n in md_h2] + ["top"]
missing = [i for i in need_ids if i not in p.ids]
broken = [h for h in toc_hrefs if h not in p.ids]

html_h2 = len(re.findall(r"<h2", src))
html_tables = src.count("<table")
html_checks = src.count('class="cb"')

print("SIZE:", len(src), "chars /", src.count("\n") + 1, "lines")
print("TAG ERRORS:", p.errors if p.errors else "none")
print("NEVER CLOSED:", never_closed if never_closed else "none")
print("MISSING IDS:", missing if missing else "none")
print("BROKEN TOC LINKS:", broken if broken else "none")
print("LEFTOVER MARKER:", "@NEXT" in src)
print("H1 COUNT:", src.count("<h1"), "(ожидается 1 — из шапки страницы)")
print("H2 SECTIONS:", html_h2, "| MD:", len(md_h2),
      "| MATCH:", html_h2 == len(md_h2))
print("TABLES:", html_tables, "| MD:", md_tables,
      "| MATCH:", html_tables == md_tables)
print("CHECK ITEMS:", html_checks, "| MD:", md_checks,
      "| MATCH:", html_checks == md_checks)

keys = [
    "YandexGPT", "Yandex Cloud", "SpeechKit", "Pipecat", "aiogram",
    "YCLIENTS", "Altegio", "AMO CRM", "I-dent", "PostgreSQL",
    "Wazzup", "152-ФЗ", "SIP", "VAD", "Docker", "Cloudflare Worker",
    "Telegram", "WhatsApp Business API", "WhatsApp",
    "1 444 СТО", "300+ клиник", "54,1 млн", "96,2 млн",
    "16 707", "17 820", "79,1%", "2,4 млн", "CVoS",
    "«Правый Поворот»", "Семейная Стоматология", "Бардж-ин",
]
absent = [k for k in keys if k not in src]
print("KEY DATA ABSENT:", absent if absent else "none")

garbage = re.findall(r"prrice|class=\"12\.12\"|c/warning|row-hz", src)
print("GARBAGE FRAGMENTS:", garbage if garbage else "none")

leftover_md = re.findall(r"(?m)^\s*\|.*\|\s*$|<p[^>]*>\*\*", src)
print("LEFTOVER MARKDOWN:", leftover_md[:3] if leftover_md else "none")

ok = (not p.errors and not never_closed and not missing and not broken
      and "@NEXT" not in src and not absent and not garbage and not leftover_md
      and html_h2 == len(md_h2) and html_tables == md_tables
      and html_checks == md_checks and src.count("<h1") == 1)
print("RESULT:", "OK" if ok else "FAIL")
sys.exit(0 if ok else 1)
