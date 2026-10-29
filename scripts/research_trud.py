#!/usr/bin/env python3
"""Research: front-desk/admin vacancies in Tyumen via Trudvsem open API.

The API caps each response at 100 rows and does not support offset, so we
query several niche keywords and merge results by vacancy id.
"""
import json
import re
import urllib.request
import urllib.parse
from collections import Counter
from datetime import datetime, timedelta

REGION = "7200000000000"  # Tyumen oblast
BASE = "http://opendata.trudvsem.ru/api/v1/vacancies/region/" + REGION
UA = {"User-Agent": "TyumenMarketResearch/1.0 (contact: hr-research@yandex.ru)"}
LOG = "project/trud_report.txt"
RAW = "project/trud_admin_tyumen.json"
DAYS = 30

KEYWORDS = [
    "администратор", "ресепшн", "офис-менеджер", "офис менеджер",
    "секретарь", "регистратор", "хостес", "оператор", "диспетчер",
    "администратор зала", "администратор салона", "администратор клиники",
]

STOP = set("""и в во не что он на я с со как а то все она так его но да ты к у же вы за бы по
только ее мне было вот от меня еще нет о из ему теперь когда даже ну вдруг ли если или
ни быть был него до вас нибудь опять уж вам ведь там потом себя ничего ей может они тут
где есть надо ней для мы тебя их чем была сам чтоб без будто чего раз тоже себе под будет
ж тогда кто этот того потому этого какой совсем ним здесь этом один почти мой тем чтобы
нее сейчас были куда зачем всех никогда можно при наконец два об другой хоть after для
его нам вас это эта эти том там чем них нему ним них работа опыт знание умение""".split())

lines = []
def out(*a):
    lines.append(" ".join(str(x) for x in a))


def get(url):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode("utf-8"))


def fetch(keyword):
    url = "%s?text=%s&limit=100" % (BASE, urllib.parse.quote(keyword))
    d = get(url)
    total = (d.get("meta") or {}).get("total", 0)
    vs = (d.get("results") or {}).get("vacancies") or []
    return total, [v.get("vacancy") or {} for v in vs]


def main():
    merged = {}
    per_kw = {}
    for kw in KEYWORDS:
        try:
            total, rows = fetch(kw)
        except Exception as e:
            out("KW_ERROR", kw, e)
            continue
        per_kw[kw] = (total, len(rows))
        for v in rows:
            vid = v.get("id")
            if vid and vid not in merged:
                merged[vid] = v

    out("PER_KEYWORD (total, fetched):")
    for kw, (t, n) in per_kw.items():
        out("  %s: %d / %d" % (kw, t, n))
    out("UNIQUE_MERGED", len(merged))

    cutoff = datetime.now() - timedelta(days=DAYS)
    active = []
    for v in merged.values():
        dm = v.get("date_modify") or v.get("creation-date") or ""
        dt = None
        for fmt, ln in (("%Y-%m-%dT%H:%M:%S", 19), ("%Y-%m-%d", 10)):
            try:
                dt = datetime.strptime(dm[:ln], fmt); break
            except ValueError:
                pass
        if dt and dt < cutoff:
            continue
        active.append(v)
    out("ACTIVE_%dD" % DAYS, len(active))

    city = []
    for v in active:
        loc = " ".join((a.get("location") or "")
                       for a in ((v.get("addresses") or {}).get("address") or []))
        if "Тюмень" in loc and "Тюменская область" not in loc:
            city.append(v)
    out("CITY_TYUMEN", len(city))

    def analyze(rows, label):
        out("\n===== %s =====" % label)
        sal = []
        exp = Counter(); sched = Counter(); comp = Counter(); words = Counter()
        for v in rows:
            if v.get("salary_min"):
                sal.append(int(v["salary_min"]))
            e = (v.get("requirement") or {}).get("experience")
            exp["опыт %s" % e] += 1
            s = v.get("schedule")
            if s: sched[s] += 1
            c = (v.get("company") or {}).get("name")
            if c: comp[c] += 1
            txt = " ".join([v.get("requirements") or "", v.get("duty") or ""])
            for w in re.findall(r"[а-яА-ЯёЁa-zA-Z]{4,}", txt.lower()):
                if w not in STOP:
                    words[w] += 1
        if sal:
            sal.sort(); n = len(sal)
            out("SALARY min=%d median=%d max=%d (n=%d)" % (sal[0], sal[n//2], sal[-1], n))
        out("EXPERIENCE:")
        for k, c in exp.most_common():
            out("  %s: %d" % (k, c))
        out("SCHEDULE:")
        for k, c in sched.most_common(8):
            out("  %s: %d" % (k, c))
        out("TOP_COMPANIES:")
        for k, c in comp.most_common(25):
            out("  %s: %d" % (k, c))
        out("TOP_WORDS:")
        for k, c in words.most_common(70):
            out("  %s: %d" % (k, c))

    analyze(active, "ALL (oblast, active)")
    analyze(city, "CITY TYUMEN")

    with open(RAW, "w", encoding="utf-8") as f:
        json.dump(active, f, ensure_ascii=False, indent=1)
    with open(LOG, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


if __name__ == "__main__":
    main()
