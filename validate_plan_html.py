import re
from html.parser import HTMLParser

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


PATH = r'C:/Users/1/Desktop/project/DEVELOPMENT_PLAN.html'
src = open(PATH, encoding='utf-8').read()

p = V()
p.feed(src)
p.close()
for t in p.stack:
    if t not in ('html', 'body'):
        p.errors.append('never closed: ' + t)

need = ['s1', 's2', 's21', 's22', 's3', 's4',
        'f0', 'f1', 'f2', 'f3', 'f4', 'f5',
        's5', 's51', 's52', 's53', 's6', 's7', 's8',
        'q', 'toc', 'top', 'clearBtn', 'menuBtn', 'scrim', 'count']
missing = [i for i in need if i not in p.ids]
broken = [h for h in p.hrefs if h not in p.ids]

print('SIZE:', len(src), 'chars /', src.count('\n') + 1, 'lines')
print('TAG ERRORS:', p.errors if p.errors else 'none')
print('MISSING IDS:', missing if missing else 'none')
print('BROKEN TOC LINKS:', broken if broken else 'none')
print('LEFTOVER MARKER:', '@NEXT' in src)
print('CHECKBOXES:', p.checkboxes, '| CHK LABELS:', p.labels,
      '| MATCH:', p.checkboxes == p.labels)
print('TABLES:', src.count('<table'),
      '| H2 SECTIONS:', len(re.findall(r'<h2', src)),
      '| COMP CARDS:', len(re.findall(r'class="comp"', src)))

keys = ['Pipecat', 'SpeechKit', 'YCLIENTS', 'Altegio', 'aiogram',
        'Wazzup', 'Mango', 'Zadarma', 'PostgreSQL', 'Yandex Cloud',
        '152-ФЗ', 'gpt-4o-mini', 'VAD', 'SIP', 'Docker',
        '12 000', '23 000', '38 000', '82 500', '5 600',
        '45–75%', 'WhatsApp', 'Telegram', 'VK', 'Instagram',
        'Латентность', 'NPS', 'бардж-ин'.title()[:1] + 'ардж-ин']
absent = [k for k in keys if k not in src]
print('KEY DATA ABSENT:', absent if absent else 'none')

garbage = re.findall(r'prrice|class="12\.12"|c/warning|row-hz', src)
print('GARBAGE FRAGMENTS:', garbage if garbage else 'none')

ok = (not p.errors and not missing and not broken and '@NEXT' not in src
      and not absent and p.checkboxes == p.labels and not garbage)
print('RESULT:', 'OK' if ok else 'FAIL')