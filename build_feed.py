# -*- coding: utf-8 -*-
"""
YML-фид для Яндекс Директа: resursstore.com (РЕСУРС)
Сайт на Тильде, доступа к админке нет — товары берём из открытого API
магазина Тильды (store.tildaapi.com), разделы каталога — со страниц сайта.

Что делает скрипт:
  1. Открывает каждую страницу каталога из CATALOG_PAGES и находит на ней
     блок магазина (storepart) Тильды.
  2. Забирает из API все товары каждого раздела.
  3. Собирает фид feed.xml:
       - <offers>      все товары (price + oldprice для распродажи);
       - <collections> страницы каталога для ЕПК, товары ссылаются на них
                       через <collectionId>.
  4. Description везде одинаковый (DESCRIPTION).
  5. Заголовки длиннее 56 символов попадают в long_titles.csv —
     их правим вручную в title_overrides.csv.

Запуск: python build_feed.py
Нужен только Python 3.9+, сторонние библиотеки не нужны.
"""

import csv
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from xml.sax.saxutils import escape

# ============================ НАСТРОЙКИ ============================

SITE = "https://resursstore.com"
BRAND = "РЕСУРС"
SHOP_NAME = BRAND
COMPANY = BRAND
VENDOR = BRAND

# Замены в названиях товаров (применяются ко всем заголовкам)
TITLE_REPLACE = {"RESURS": BRAND}
CURRENCY = "RUB"

# Одинаковое описание для всех товаров и страниц каталога
DESCRIPTION = "Украшения для тех, кто выбирает свободу быть собой"

# Лимит заголовка в товарных объявлениях Директа
TITLE_LIMIT = 56

# Сколько фото отдавать на товар и на страницу каталога (Директ рекомендует до 5)
MAX_PICTURES = 5

# Страницы каталога.
# id — латиницей (до 60 символов); category — название категории в фиде;
# name — заголовок объявления страницы каталога (до 56 символов); url — страница на сайте.
# collection: True  — страница попадает в фид как страница каталога (<collection>);
#             False — раздел только используется для сбора товаров, в рекламу не идёт.
# Порядок важен: первый подходящий раздел станет основной категорией товара.
CATALOG_PAGES = [
    {"id": "earrings",  "category": "Серьги",   "name": "Серьги РЕСУРС",   "url": "/catalog/Earrings",  "collection": True},
    {"id": "chokers",   "category": "Чокеры",   "name": "Чокеры РЕСУРС",   "url": "/catalog/chokers",   "collection": True},
    {"id": "chains",    "category": "Подвесы",  "name": "Подвесы РЕСУРС",  "url": "/catalog/Chains",    "collection": True},
    {"id": "bracelets", "category": "Браслеты", "name": "Браслеты РЕСУРС", "url": "/catalog/Bracelets", "collection": True},
    {"id": "rings",     "category": "Кольца",   "name": "Кольца РЕСУРС",   "url": "/catalog/rings",     "collection": True},
    {"id": "cuffs",     "category": "Каффы",    "name": "Каффы РЕСУРС",    "url": "/catalog/Cuffs",     "collection": True},
    {"id": "anklets",   "category": "Анклеты",  "name": "Анклеты РЕСУРС",  "url": "/catalog/anklets",   "collection": True},
    {"id": "charms",    "category": "Обвесы",   "name": "Обвесы РЕСУРС",   "url": "/catalog/charms",    "collection": True},
    {"id": "for-him",   "category": "Для него", "name": "Украшения для него РЕСУРС", "url": "/catalog/for-him", "collection": True},
    # Ниже — разделы без страницы каталога в рекламе: товары из них в фиде остаются
    {"id": "stones",    "category": "Камни",    "name": "Камни РЕСУРС",    "url": "/catalog/stones",    "collection": False},
    {"id": "sets",      "category": "Сеты",     "name": "Сеты РЕСУРС",     "url": "/catalog/sets",      "collection": False},
    {"id": "all",       "category": "",         "name": "Все украшения",   "url": "/catalog",           "collection": False},
]

# Если на какой-то странице скрипт не найдёт блок магазина автоматически,
# сюда можно вписать его вручную: "/catalog/sets": ["133001602081"]
MANUAL_STOREPARTS = {
    # "/catalog/sets": ["133001602081"],
}

# Товары, в названии которых есть эти слова, в фид не попадают
# (по умолчанию — подарочные сертификаты; чтобы включить их, оставьте список пустым)
EXCLUDE_TITLE_WORDS = ["сертификат"]

# Файлы
OUT_FEED = "feed.xml"
OUT_LONG_TITLES = "long_titles.csv"
OVERRIDES_FILE = "title_overrides.csv"

# ===================================================================

API_URL = "https://store.tildaapi.com/api/getproductslist/"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
MSK = timezone(timedelta(hours=3))


def log(*a):
    print(*a, flush=True)


def http_get(url, referer=None, tries=3):
    headers = {"User-Agent": UA, "Accept-Language": "ru-RU,ru;q=0.9"}
    if referer:
        headers["Referer"] = referer
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=40) as r:
                return r.read().decode("utf-8", errors="replace")
        except Exception as e:  # noqa
            last = e
            time.sleep(2 * (i + 1))
    raise RuntimeError(f"Не удалось открыть {url}: {last}")


# ---------- поиск блоков магазина на странице каталога ----------

STOREPART_RE = re.compile(r"""storepart(?:uid|_uid)?["']?\s*[:=]\s*["'](\d{6,})["']""", re.I)
REC_RE = re.compile(r"""id=["']rec(\d+)["']""")


def find_storeparts(html):
    """Возвращает список (storepart, recid) из HTML страницы каталога."""
    recs = [(m.start(), m.group(1)) for m in REC_RE.finditer(html)]
    found, seen = [], set()
    for m in STOREPART_RE.finditer(html):
        sp = m.group(1)
        if sp in seen:
            continue
        seen.add(sp)
        recid = ""
        for pos, rid in recs:  # ближайший блок rec… перед упоминанием storepart
            if pos < m.start():
                recid = rid
            else:
                break
        found.append((sp, recid))
    return found


# ---------- API Тильды ----------

def fetch_part_products(storepart, recid, referer):
    products, slice_n, size = [], 1, 100
    while True:
        params = {
            "storepartuid": storepart,
            "getparts": "true",
            "getoptions": "true",
            "slice": slice_n,
            "size": size,
            "c": int(time.time() * 1000),
        }
        if recid:
            params["recid"] = recid
        url = API_URL + "?" + urllib.parse.urlencode(params)
        data = json.loads(http_get(url, referer=referer))
        batch = data.get("products") or []
        products.extend(batch)
        total = int(data.get("total") or 0)
        if not batch or len(products) >= total or not data.get("nextslice"):
            break
        slice_n = data.get("nextslice") or slice_n + 1
        time.sleep(0.5)
    return products


# ---------- нормализация данных ----------

def to_num(v):
    """'13 800,00' / '9770.0000' / '' -> float | None"""
    if v is None:
        return None
    s = re.sub(r"\s", "", str(v)).replace(" ", "").replace(",", ".")
    if not s:
        return None
    try:
        n = float(s)
    except ValueError:
        return None
    return n if n > 0 else None


def fmt_price(n):
    return str(int(n)) if float(n).is_integer() else f"{n:.2f}"


def clean_text(s):
    s = re.sub(r"<[^>]+>", " ", s or "")
    s = s.replace("&nbsp;", " ").replace(" ", " ")
    return re.sub(r"\s+", " ", s).strip()


def normalize_title(t):
    """RESURS -> РЕСУРС; латинские слова капсом (BOLD, LIGHT, MEDIUM) -> строчными,
    Директ не пропускает капс, кроме аббревиатур. Однобуквенные размеры (M, L) не трогаем."""
    for a, b in TITLE_REPLACE.items():
        t = t.replace(a, b)
    return re.sub(r"\b[A-Z]{3,}\b", lambda m: m.group(0).lower(), t)


def product_prices(p):
    """Берём самый дешёвый вариант (edition) и его старую цену."""
    best = None
    for ed in p.get("editions") or [{}]:
        price = to_num(ed.get("price")) or to_num(p.get("price"))
        old = to_num(ed.get("priceold")) or to_num(p.get("priceold"))
        if price is None:
            continue
        if best is None or price < best[0]:
            best = (price, old)
    if best is None:
        return None, None
    price, old = best
    if old is not None and old <= price:
        old = None  # Директ требует oldprice > price
    return price, old


def product_available(p):
    eds = p.get("editions") or [p]
    for ed in eds:
        q = str(ed.get("quantity", "")).strip()
        if q == "" or (q.lstrip("-").isdigit() and int(q) != 0):
            return True  # пусто = без учёта остатков, считаем в наличии
    return False


def product_pictures(p):
    pics = []
    for g in p.get("gallery") or []:
        img = g.get("img") if isinstance(g, dict) else None
        if img and img not in pics:
            pics.append(img)
    if isinstance(p.get("gallery"), str):  # иногда gallery приходит строкой JSON
        try:
            for g in json.loads(p["gallery"]):
                if g.get("img") and g["img"] not in pics:
                    pics.append(g["img"])
        except Exception:
            pass
    return pics[:MAX_PICTURES]


def product_url(p):
    url = (p.get("url") or "").strip()
    if url.startswith("/"):
        url = SITE + url
    return url


# ---------- ручные заголовки ----------

def load_overrides():
    by_uid, by_title = {}, {}
    if not os.path.exists(OVERRIDES_FILE):
        return by_uid, by_title
    with open(OVERRIDES_FILE, encoding="utf-8-sig", newline="") as f:
        sample = f.read(2048)
        f.seek(0)
        delim = ";" if sample.count(";") >= sample.count(",") else ","
        for row in csv.DictReader(f, delimiter=delim):
            new = (row.get("title_feed") or "").strip()
            if not new:
                continue
            uid = (row.get("uid") or "").strip()
            old = (row.get("title_site") or "").strip()
            if uid:
                by_uid[uid] = new
            elif old:
                by_title[old.lower()] = new
    return by_uid, by_title


# ---------- сборка ----------

def collect():
    offers = {}          # uid -> product
    membership = {}      # uid -> [collection ids]
    first_url = {}       # uid -> url
    coll_pics = {}       # collection id -> [pictures]
    problems = []

    for page in CATALOG_PAGES:
        page_url = SITE + page["url"]
        cid = page["id"]
        parts = [(sp, "") for sp in MANUAL_STOREPARTS.get(page["url"], [])]
        if not parts:
            try:
                parts = find_storeparts(http_get(page_url))
            except Exception as e:
                problems.append(f"{page_url}: страница не открылась ({e})")
                continue
        if not parts:
            problems.append(f"{page_url}: не найден блок магазина — "
                            f"впишите storepart в MANUAL_STOREPARTS")
            continue

        count = 0
        for sp, recid in parts:
            try:
                items = fetch_part_products(sp, recid, page_url)
            except Exception as e:
                problems.append(f"{page_url}: API storepart={sp}: {e}")
                continue
            for p in items:
                uid = str(p.get("uid"))
                if not uid or uid == "None":
                    continue
                offers.setdefault(uid, p)
                if page["id"] != "all" or uid not in first_url:
                    first_url.setdefault(uid, product_url(p))
                membership.setdefault(uid, [])
                if cid not in membership[uid]:
                    membership[uid].append(cid)
                count += 1
                pics = coll_pics.setdefault(cid, [])
                if len(pics) < MAX_PICTURES:
                    pp = product_pictures(p)
                    if pp and pp[0] not in pics:
                        pics.append(pp[0])
        log(f"  {page['name']:<22} {page_url}  storepart={','.join(s for s, _ in parts)}  товаров: {count}")

    return offers, membership, first_url, coll_pics, problems


def build():
    log("Собираю товары с resursstore.com …")
    offers, membership, first_url, coll_pics, problems = collect()
    by_uid, by_title = load_overrides()

    cat_pages = [p for p in CATALOG_PAGES if p.get("category")]
    cat_ids = {page["id"]: i + 2 for i, page in enumerate(cat_pages)}  # 1 = корень
    coll_pages = {p["id"] for p in CATALOG_PAGES if p.get("collection")}
    order = [page["id"] for page in CATALOG_PAGES]
    active_colls = {c for cs in membership.values() for c in cs}

    long_titles, offers_xml, skipped = [], [], []
    sale_count = 0

    for uid, p in offers.items():
        site_title = clean_text(p.get("title"))
        if any(w.lower() in site_title.lower() for w in EXCLUDE_TITLE_WORDS):
            skipped.append((uid, site_title, "исключён по слову в названии"))
            continue
        price, old = product_prices(p)
        if price is None:
            skipped.append((uid, site_title, "нет цены"))
            continue
        url = first_url.get(uid) or product_url(p)
        if not url:
            skipped.append((uid, site_title, "нет ссылки"))
            continue
        pics = product_pictures(p)

        title = normalize_title(by_uid.get(uid) or by_title.get(site_title.lower()) or site_title)
        if len(title) > TITLE_LIMIT:
            long_titles.append((uid, site_title, len(title), url))

        sections = sorted(membership.get(uid, []), key=order.index)
        main = next((c for c in sections if c in cat_ids), None)
        cat = cat_ids[main] if main else 1
        colls = [c for c in sections if c in coll_pages]

        if old:
            sale_count += 1
        lines = [f'    <offer id="{escape(uid)}" available="{"true" if product_available(p) else "false"}">',
                 f"      <name>{escape(title)}</name>",
                 f"      <vendor>{escape(VENDOR)}</vendor>",
                 f"      <url>{escape(url)}</url>",
                 f"      <price>{fmt_price(price)}</price>"]
        if old:
            lines.append(f"      <oldprice>{fmt_price(old)}</oldprice>")
        lines += [f"      <currencyId>{CURRENCY}</currencyId>",
                  f"      <categoryId>{cat}</categoryId>"]
        lines += [f"      <picture>{escape(x)}</picture>" for x in pics]
        lines.append(f"      <description>{escape(DESCRIPTION)}</description>")
        lines += [f"      <collectionId>{escape(c)}</collectionId>" for c in colls]
        lines.append("    </offer>")
        offers_xml.append("\n".join(lines))

    # категории
    cats_xml = ['    <category id="1">Украшения</category>']
    for page in cat_pages:
        cats_xml.append(f'    <category id="{cat_ids[page["id"]]}" parentId="1">{escape(page["category"])}</category>')

    # коллекции = страницы каталога
    colls_xml = []
    for page in CATALOG_PAGES:
        cid = page["id"]
        if cid not in coll_pages or cid not in active_colls:
            continue
        if len(page["name"]) > TITLE_LIMIT:
            problems.append(f"Название страницы каталога длиннее {TITLE_LIMIT}: {page['name']}")
        c = [f'    <collection id="{escape(cid)}">',
             f"      <url>{escape(SITE + page['url'])}</url>",
             f"      <name>{escape(page['name'])}</name>",
             f"      <description>{escape(DESCRIPTION)}</description>"]
        c += [f"      <picture>{escape(x)}</picture>" for x in coll_pics.get(cid, [])]
        c.append("    </collection>")
        colls_xml.append("\n".join(c))

    now = datetime.now(MSK).strftime("%Y-%m-%d %H:%M")
    xml = "\n".join([
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<yml_catalog date="{now}">',
        "  <shop>",
        f"    <name>{escape(SHOP_NAME)}</name>",
        f"    <company>{escape(COMPANY)}</company>",
        f"    <url>{SITE}</url>",
        "    <currencies>",
        f'      <currency id="{CURRENCY}" rate="1"/>',
        "    </currencies>",
        "    <categories>",
        *["  " + x for x in cats_xml],
        "    </categories>",
        "    <offers>",
        *["  " + x.replace("\n", "\n  ") for x in offers_xml],
        "    </offers>",
        "    <collections>",
        *["  " + x.replace("\n", "\n  ") for x in colls_xml],
        "    </collections>",
        "  </shop>",
        "</yml_catalog>",
        "",
    ])

    if not offers_xml:
        log("ОШИБКА: не найдено ни одного товара, фид не перезаписан.")
        for pr in problems:
            log("  -", pr)
        sys.exit(1)

    # Не перезаписываем файл, если изменилась только дата (меньше лишних коммитов)
    def strip_date(s):
        return re.sub(r'<yml_catalog date="[^"]*">', "", s)
    if os.path.exists(OUT_FEED):
        with open(OUT_FEED, encoding="utf-8") as f:
            if strip_date(f.read()) == strip_date(xml):
                log("Фид не изменился.")
                xml = None
    if xml:
        with open(OUT_FEED, "w", encoding="utf-8") as f:
            f.write(xml)

    with open(OUT_LONG_TITLES, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["uid", "title_site", "length", "url"])
        w.writerows(sorted(long_titles, key=lambda r: -r[2]))

    log("")
    log(f"Товаров в фиде: {len(offers_xml)} (со скидкой, с oldprice: {sale_count})")
    log(f"Страниц каталога в фиде: {len(colls_xml)}")
    log(f"Заголовков длиннее {TITLE_LIMIT} символов без ручной правки: {len(long_titles)} -> {OUT_LONG_TITLES}")
    for uid, t, n, _ in long_titles:
        log(f"  [{n}] {t}  (uid {uid})")
    if skipped:
        log(f"Пропущено товаров: {len(skipped)}")
        for uid, t, why in skipped:
            log(f"  {t} (uid {uid}): {why}")
    if problems:
        log("Предупреждения:")
        for pr in problems:
            log("  -", pr)


if __name__ == "__main__":
    build()
