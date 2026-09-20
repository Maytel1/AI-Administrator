#!/usr/bin/env python3
"""Research: administrator vacancies in Tyumen via public hh.ru API."""
import json
import re
import urllib.request
import urllib.parse
from collections import Counter

UA = {"User-Agent": "TyumenMarketResearch/1.0 (contact: hr-research@yandex.ru)"}
AREA_ID = "95"  # Tyumen
AREA_NAME = "Тюмень"
ROLE = "администратор"
OUT = "project/hh_admin_tyumen.json"
LOG = "project/hh_report.txt"
STOP = set("""и в во не что он на я с со как а то все она так его но да ты к у же вы за бы по
только ее мне было вот от меня еще нет о из ему теперь когда даже ну вдруг ли если или
ни быть был него до вас нибудь опять уж вам ведь там потом себя ничего ей может они тут
где есть надо ней для мы тебя их чем была сам чтоб без будто чего раз тоже себе под будет
ж тогда кто этот того потому этого какой совсем ним здесь этом один почти мой тем чтобы
нее сейчас были куда зачем всех никогда можно при наконец два об другой хоть after для
его нам вас это эта эти том там чем них нему ним них""".split())

lines = []
def out(*a):
    s = " ".join(str(x) for x in a)
    lines.append(s)
    print(s)


def get(url):
    req = urllib.request.Request(url, headers=UA)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        raise RuntimeError("HTTP %s for %s BODY: %s" % (e.code, url, body))


def find_area(text):
    data = get("https://api.hh.ru/suggests/areas?text=" + urllib.parse.quote(text))
    for it in data.get("items", []):
        if text.lower() in it.get("text", "").lower():
            return it.get("id"), it.get("text")
    return (data.get("items") or [{}])[0].get("id"), None


def main():
    area_id, area_name = AREA_ID, AREA_NAME
    out("AREA", area_id, area_name)

    items = []
    found = 0
    page = 0
    while page < 5:
        url = ("https://api.hh.ru/vacancies?text=%s&area=%s&period=30&per_page=100&page=%d"
               % (urllib.parse.quote(ROLE), area_id, page))
        data = get(url)
        found = data.get("found", 0)
        batch = data.get("items", [])
        items.extend(batch)
        if len(batch) < 100:
            break
        page += 1

    out("FOUND_TOTAL_30D", found)
    out("COLLECTED", len(items))

    exp = Counter(); sched = Counter(); employers = Counter(); words = Counter()
    sal = []
    for v in items:
        e = (v.get("experience") or {}).get("name")
        if e: exp[e] += 1
        s = (v.get("schedule") or {}).get("name")
        if s: sched[s] += 1
        emp = (v.get("employer") or {}).get("name")
        if emp: employers[emp] += 1
        sv = v.get("salary") or {}
        if sv.get("from"): sal.append(sv["from"])
        sn = v.get("snippet") or {}
        text = " ".join([sn.get("requirement") or "", sn.get("responsibility") or ""])
        text = re.sub(r"<[^>]+>", " ", text)
        for w in re.findall(r"[а-яА-ЯёЁa-zA-Z]{4,}", text.lower()):
            if w not in STOP:
                words[w] += 1

    out("\nEXPERIENCE:")
    for k, c in exp.most_common():
        out("  %s: %d" % (k, c))
    out("\nSCHEDULE:")
    for k, c in sched.most_common():
        out("  %s: %d" % (k, c))
    if sal:
        sal.sort(); n = len(sal)
        out("\nSALARY_FROM: min=%d median=%d max=%d (n=%d)"
            % (sal[0], sal[n // 2], sal[-1], n))
    out("\nTOP_EMPLOYERS:")
    for k, c in employers.most_common(20):
        out("  %s: %d" % (k, c))
    out("\nTOP_WORDS:")
    for k, c in words.most_common(60):
        out("  %s: %d" % (k, c))

    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=1)
    with open(LOG, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


if __name__ == "__main__":
    main()