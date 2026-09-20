#!/usr/bin/env python3
"""Analyze Tyumen admin vacancies: city, SMB vs chains, service sector."""
import json, re
from collections import Counter

RAW = "project/trud_admin_tyumen.json"
LOG = "project/trud_analysis.txt"
CHAINS = ["fix price","пятёрочка","пятерочка","магнит","ашан","м.видео","эльдорадо",
          "лента","перекресток","перекрёсток","дикси","вкусвилл","окей","о'кей",
          "метро","самокат","wildberries","вайлдберриз","ozon","озон"]
SERVICE = {
 "салон/красота":["салон","красот","ногт","парикмахер","массаж","космет","бров","ресниц","spa","спа","барбер","студи"],
 "медицина":["клиник","медицин","стоматолог","больниц","поликлиник","диагност","лаборатор"],
 "отель":["отел","гостиниц","хостел","апартамент","туризм"],
 "автосервис":["автосервис","автотехцентр","шиномонтаж","автомойк","автошкол","автосалон"],
 "фитнес":["фитнес","спорт","тренаж","бассейн","йог","танц"],
 "образование":["школ","центр развития","курс","обучени","репетитор","детск","садик"],
 "недвижимость":["недвижим","агентств","риэлтор","застройщик"],
 "услуги":["сервис","услуг","ремонт","ателье","химчистк","прачечн"],
}
CALL = ["звонк","телефон","обзвон","консультир","клиент","запис","бронир","встреч",
        "прием","приём","ресепшн","ресепшен","посетител","обращени","заявк","диспетчер"]

def is_chain(n): 
    n=(n or "").lower(); return any(c in n for c in CHAINS)

def svc_of(v):
    t=" ".join([v.get("job-name") or "",v.get("duty") or "",v.get("requirements") or "",
                (v.get("company") or {}).get("name") or ""]).lower()
    return [c for c,k in SERVICE.items() if any(x in t for x in k)]

def main():
    data=json.load(open(RAW,encoding="utf-8")); out=[]
    def p(*a): out.append(" ".join(str(x) for x in a))
    p("TOTAL",len(data))
    city=[v for v in data if re.search(r"г\.?\s*Тюмень", " ".join((a.get("location") or "") for a in ((v.get("addresses") or {}).get("address") or [])))]
    p("CITY_TYUMEN",len(city))
    smb=[v for v in city if not is_chain((v.get("company") or {}).get("name"))]
    p("CITY_SMB",len(smb))
    svc=[]
    for v in smb:
        c=svc_of(v)
        if c: v["_svc"]=c; svc.append(v)
    p("CITY_SMB_SERVICE",len(svc))
    call=[v for v in smb if any(w in " ".join([v.get("duty") or "",v.get("requirements") or ""]).lower() for w in CALL)]
    p("CITY_SMB_CALL",len(call))
    def stats(rows,label):
        p("\n== %s (n=%d) =="%(label,len(rows)))
        sal=[];exp=Counter();sch=Counter();comp=Counter();cat=Counter()
        for v in rows:
            if v.get("salary_min"): sal.append(int(v["salary_min"]))
            exp["опыт %s"%((v.get("requirement") or {}).get("experience"))]+=1
            if v.get("schedule"): sch[v["schedule"]]+=1
            if (v.get("company") or {}).get("name"): comp[v["company"]["name"]]+=1
            for x in v.get("_svc",[]): cat[x]+=1
        if sal: sal.sort();n=len(sal);p("SALARY min=%d med=%d max=%d n=%d"%(sal[0],sal[n//2],sal[-1],n))
        p("EXP",dict(exp.most_common()));p("SCHED",dict(sch.most_common(6)))
        if cat: p("CATS",dict(cat.most_common()))
        p("COMPANIES:")
        for k,c in comp.most_common(25): p("  %s: %d"%(k,c))
    stats(smb,"CITY SMB"); stats(svc,"CITY SMB SERVICE")
    p("\n== SAMPLE SERVICE ==")
    for v in svc[:40]:
        p("- %s | %s | %s | %s"%(v.get("job-name"),(v.get("company") or {}).get("name"),v.get("salary"),",".join(v.get("_svc",[]))))
    open(LOG,"w",encoding="utf-8").write("\n".join(out))

if __name__=="__main__": main()