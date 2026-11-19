import csv, json, os, re, sys, time, statistics, urllib.request, urllib.error
from datetime import datetime, timezone, timedelta
from urllib.parse import urlparse

SRC, OUT = sys.argv[1], sys.argv[2]
SKIPPED = OUT.rsplit(".", 1)[0] + "-skipped.txt"

class RateLimited(Exception):
    pass
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"
GAP = 4

CATEGORIES = {
    "sleep": r"sleep\w*|swaddles?|bassinets?|cribs?|sheets?|mattress(?:es)?|sleep ?sacks?|pajamas?|pyjamas?|sound machines?|night ?lights?",
    "feeding": r"feeding|bottles?|nipples?|bibs?|bowls?|plates?|spoons?|sippy|cups?|high ?chairs?|breast\w*|pumps?|nursing|formula|teethers?|teething|pacifiers?|soothers?",
    "gear": r"strollers?|car ?seats?|carriers?|wraps?|slings?|wagons?|travel|playards?|play ?yards?|bouncers?|swings?|rockers?|walkers?|monitors?|diaper bags?",
    "nursery": r"nursery|furniture|dressers?|gliders?|changing|bedding|blankets?|decor|storage|baskets?|mobiles?",
    "bath_diaper": r"bath|tubs?|towels?|diapers?|wipes?|potty|lotions?|body wash|shampoo|balms?|skincare|swim diapers?",
    "apparel": r"apparel|clothing|onesies?|bodysuits?|rompers?|hats?|socks?|shoes?|dress(?:es)?|leggings?|sunglasses|headbands?|bows?|outfits?|swimwear",
    "toys_play": r"toys?|play ?mats?|playmats?|books?|rattles?|plush|stuffed|puzzles?|activity|gyms?",
    "health_safety": r"thermometers?|nasal|aspirators?|safety|gates?|locks?|first aid|humidifiers?|medicine|sun ?protection|ear ?muffs?",
}
CAT_RX = {c: re.compile(rf"\b(?:{rx})\b", re.I) for c, rx in CATEGORIES.items()}

APP_SIGNATURES = {
    "Swym": r"swym(?:cdn|relay|-|\.)",
    "MyRegistry": r"myregistry\.com",
    "Babylist button": r"babylist\.com/(?:add|button|bookmarklet|api)|add-to-babylist",
    "Zola": r"zola\.com/(?:registry|widget)",
    "Wishlist King": r"wishlist-king|appmate\.io",
    "Growave": r"growave\.io",
    "Wishlist Hero": r"wishlisthero",
    "Smart Wishlist": r"smartwishlist",
    "Hulk Wishlist": r"hulkapps\.com.*wishlist",
    "Wishlist Plus (Swym)": r"wishlistplus",
}

log = lambda *a: print(*a, file=sys.stderr, flush=True)

def get(url, tries=2):
    for attempt in range(tries):
        time.sleep(GAP)
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.status, r.read().decode("utf-8", "replace"), r.url
        except urllib.error.HTTPError as e:
            if e.code == 429:
                wait = min(300, max(int(e.headers.get("Retry-After", "60")) + 5, 65 * 2 ** attempt))
                log(f"  429 on {url}, waiting {wait}s")
                time.sleep(wait)
                continue
            return e.code, "", url
        except Exception as e:
            log(f"  error {url}: {e}")
            return None, "", url
    return 429, "", url

def fetch_products(base):
    products, page = [], 1
    while True:
        code, body, _ = get(f"{base}/products.json?limit=250&page={page}&country=US")
        if code != 200:
            return products, f"products.json HTTP {code} on page {page}"
        batch = json.loads(body).get("products", [])
        products += batch
        log(f"  page {page}: {len(batch)}")
        if len(batch) < 250:
            return products, ""
        page += 1

def classify(p):
    text = " ".join([p.get("product_type") or "", p.get("title") or "", " ".join(p.get("tags") or [])])
    return {c for c, rx in CAT_RX.items() if rx.search(text)}

def parse_dt(s):
    return datetime.fromisoformat(s) if s else None

def registry_links(html):
    links = set()
    for href, text in re.findall(r'<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', html, re.S | re.I):
        t = re.sub(r"<[^>]+>|\s+", " ", text).strip()
        if re.search(r"registry", t + " " + href, re.I) and not re.search(r"product registr|warranty|register your", t, re.I):
            links.add(f"{t[:40]} -> {href[:80]}")
    return sorted(links)

with open(OUT, newline="") as f:
    reader = csv.DictReader(f)
    FIELDS = reader.fieldnames
    done = {r["brand"] for r in reader if r.get("brand")}
SHARD, NSHARDS, LIMIT = int(os.environ.get("SHARD", 0)), int(os.environ.get("NSHARDS", 1)), int(os.environ.get("LIMIT", 0))
rows = [r for i, r in enumerate(csv.DictReader(open(SRC, newline=""))) if i % NSHARDS == SHARD and r["brand"] not in done]
rows = rows[:LIMIT] if LIMIT else rows
log(f"{len(done)} already in {OUT}, {len(rows)} to go")
now = datetime.now(timezone.utc)
def score(r):
    code, html, final = get(r["url"])
    fu = urlparse(final)
    base = f"{fu.scheme}://{fu.netloc}"
    log(f"{r['brand']} ({base})")
    rec = {"brand": r["brand"], "url": r["url"], "resolved_base": base, "platform": r["platform"]}

    products, err = fetch_products(base)
    if "HTTP 429" in err:
        raise RateLimited(err)
    products = [p for p in products if "gift card" not in (p.get("title") or "").lower() and (p.get("product_type") or "").lower() != "gift card"]
    variants = [v for p in products for v in p.get("variants", [])]
    prices = sorted(float(v["price"]) for v in variants if v.get("price") not in (None, "") and float(v["price"]) > 0)

    cat_counts = {}
    for p in products:
        for c in classify(p):
            cat_counts[c] = cat_counts.get(c, 0) + 1
    unclassified = sum(1 for p in products if not classify(p))
    types = {}
    for p in products:
        t = (p.get("product_type") or "").strip() or "(blank)"
        types[t] = types.get(t, 0) + 1

    created = [parse_dt(p.get("created_at")) for p in products if p.get("created_at")]
    pub = [parse_dt(p.get("published_at")) for p in products if p.get("published_at")]
    pct_in_stock = round(100 * sum(1 for v in variants if v.get("available")) / len(variants), 1) if variants else ""

    apps = [name for name, rx in APP_SIGNATURES.items() if re.search(rx, html, re.I)]
    reg = registry_links(html)
    where_to_buy = bool(re.search(r"where to buy|find a retailer|store locator", html, re.I))
    sells_direct = bool(variants) and pct_in_stock != 0 and len(set(prices)) > 1

    rec.update({
        "fetch_error": err,
        "sells_direct": sells_direct,
        "where_to_buy_link": where_to_buy,
        "product_count": len(products),
        "variant_count": len(variants),
        "pct_variants_in_stock": pct_in_stock,
        "registry_categories_count": len(cat_counts),
        "registry_categories": "; ".join(f"{c}:{n}" for c, n in sorted(cat_counts.items(), key=lambda x: -x[1])),
        "pct_products_unclassified": round(100 * unclassified / len(products), 1) if products else "",
        "top_product_types": "; ".join(f"{t}:{n}" for t, n in sorted(types.items(), key=lambda x: -x[1])[:6]),
        "price_min_usd": prices[0] if prices else "",
        "price_median_usd": round(statistics.median(prices), 2) if prices else "",
        "price_max_usd": prices[-1] if prices else "",
        "pct_variants_under_50": round(100 * sum(1 for x in prices if x < 50) / len(prices), 1) if prices else "",
        "pct_variants_over_150": round(100 * sum(1 for x in prices if x > 150) / len(prices), 1) if prices else "",
        "latest_product_created": max(created).date().isoformat() if created else "",
        "products_created_last_180d": sum(1 for d in created if d >= now - timedelta(days=180)),
        "latest_product_published": max(pub).date().isoformat() if pub else "",
        "homepage_http": code,
        "existing_apps_detected": "; ".join(apps) or "none",
        "registry_links_on_homepage": " | ".join(reg),
    })
    return rec

with open(OUT, "a", newline="") as f:
    w = csv.DictWriter(f, fieldnames=FIELDS, restval="")
    for i, r in enumerate(rows, 1):
        try:
            rec = score(r)
        except RateLimited as e:
            log(f"  SKIPPED {i}/{len(rows)} {r['brand']} | {e}")
            with open(SKIPPED, "a") as s:
                s.write(r["brand"] + "\n")
            continue
        except Exception as e:
            log(f"  FAILED {r['brand']}: {e}")
            rec = {"brand": r["brand"], "url": r["url"], "platform": r["platform"], "fetch_error": f"script error: {e}"}
        w.writerow(rec); f.flush()
        log(f"  WROTE {i}/{len(rows)} {r['brand']} | {rec.get('fetch_error') or 'ok'}")
log("done")
