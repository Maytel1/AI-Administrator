#!/usr/bin/env python3
"""Collect Tyumen admin vacancies from hh.ru API using an APPLICATION token.

Auth: OAuth2 client_credentials (app authorization). No user/browser needed.
Register an app at https://dev.hh.ru/admin to get client_id / client_secret.

Credentials are read from (in order):
  1) env vars HH_CLIENT_ID / HH_CLIENT_SECRET
  2) file project/hh_credentials.json  -> {"client_id": "...", "client_secret": "..."}

Usage:
  python project\\research_hh_api.py
"""
import json
import os
import re
import urllib.request
import urllib.parse
from collections import Counter

TOKEN_URL = "https://api.hh.ru/token"
API = "https://api.hh.ru"
AREA_TYUMEN = "95"
ROLE = "администратор"
CRED_FILE = "project/hh_credentials.json"
RAW = "project/hh_admin_tyumen.json"
LOG = "project/hh_api_report.txt"
UA = "TyumenMarketResearch/1.0 (contact: hr-research@yandex.ru)"

STOP = set("""и в во не что он на я с со как а то все она так его но да ты к у же вы за бы по
только ее мне было вот от меня еще нет о из ему теперь когда даже ну вдруг ли если или
ни быть был него до вас нибудь опять уж вам ведь там потом себя ничего ей может они тут
где есть надо ней для мы тебя их чем была сам чтоб без будто чего раз тоже себе под будет
ж тогда кто этот того потому этого какой совсем ним здесь этом один почти мой тем чтобы
нее сейчас были куда зачем всех никогда можно при наконец два об другой хоть для его нам
вас это эта эти том там чем них нему ним работа опыт знание умение""".split())

lines = []
def out(*a):
    s = " ".join(str(x) for x in a)
    lines.append(s)
    print(s)


def load_creds():
    cid = os.environ.get("HH_CLIENT_ID")
    sec = os.environ.get("HH_CLIENT_SECRET")
    if cid and sec:
        return cid, sec
    if os.path.exists(CRED_FILE):
        d = json.load(open(CRED_FILE, encoding="utf-8"))
        return d.get("client_id"), d.get("client_secret")
    return None, None


def get_app_token(cid, sec):
    body = urllib.parse.urlencode({
        "grant_type": "client_credentials",
        "client_id": cid,
        "client_secret": sec,
    }).encode("utf-8")
    req = urllib.request.Request(TOKEN_URL, data=body, headers={
        "User-Agent": UA,
        "Content-Type": "application/x-www-form-urlencoded",
    })
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8")).get("access_token")


def api_get(path, params, token):
    url = API + path + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Authorization": "Bearer " + token,
    })
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        raise RuntimeError("HTTP %s %s BODY: %s" % (e.code, url, body))


def main():
    cid, sec = load_creds()
    if not cid or not sec:
        out("NO_CREDENTIALS: создайте проект/hh_credentials.json "
            "или задайте HH_CLIENT_ID/HH_CLIENT_SECRET. "
            "Регистрация приложения: https://dev.hh.ru/admin")
        open(LOG, "w", encoding="utf-8").write("\n".join(lines))
        return

    token = get_app_token(cid, sec)
    out("TOKEN_OK", bool(token))

    items = []
    page = 0
    while page < 5:
        d = api_get("/vacancies", {
            "text": ROLE, "area": AREA_TYUMEN,
            "period": 30, "per_page": 100, "page": page,
        }, token)
        found = d.get("found", 0)
        batch = d.get("items", [])
        items.extend(batch)
        if page == 0:
            out("FOUND_TOTAL_30D", found)
        if len(batch) < 100:
            break
        page += 1
    out("COLLECTED", len(items))

    exp = Counter(); sched = Counter(); emp = Counter(); words = Counter()
    sal = []
    for v in items:
        e = (v.get("experience") or {}).get("name")
        if e: exp[e] += 1
        s = (v.get("schedule") or {}).get("name")
        if s: sched[s] += 1
        n = (v.get("employer") or {}).get("name")
        if n: emp[n] += 1
        sv = v.get("salary") or {}
        if sv.get("from"): sal.append(sv["from"])
        sn = v.get("snippet") or {}
        txt = " ".join([sn.get("requirement") or "", sn.get("responsibility") or ""])
        txt = re.sub(r"<[^>]+>", " ", txt)
        for w in re.findall(r"[а-яА-ЯёЁa-zA-Z]{4,}", txt.lower()):
            if w not in STOP:
                words[w] += 1

    out("\nEXPERIENCE:", dict(exp.most_common()))
    out("SCHEDULE:", dict(sched.most_common(8)))
    if sal:
        sal.sort(); n = len(sal)
        out("SALARY_FROM min=%d med=%d max=%d n=%d" % (sal[0], sal[n//2], sal[-1], n))
    out("TOP_EMPLOYERS:")
    for k, c in emp.most_common(25):
        out("  %s: %d" % (k, c))
    out("TOP_WORDS:", dict(words.most_common(60)))

    json.dump(items, open(RAW, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    open(LOG, "w", encoding="utf-8").write("\n".join(lines))


if __name__ == "__main__":
    main()