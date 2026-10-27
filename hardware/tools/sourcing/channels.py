"""Channel adapters: fetch (with a dated on-disk cache) and parse distributor listings.

Keyless channels, always read:
  digikey    Digi-Key's public search and product pages (the __NEXT_DATA__ JSON they embed)
  findchips  the FindChips aggregator page (Mouser, Newark, Arrow, TTI, Future, TME, RS and
             brokers, each with FindChips' "Authorized Distributor" marker)
  lcsc       the JLCPCB parts API (selectSmtComponentList), which carries LCSC stock and prices

Keyed channels, read only when their variables are set (skipped otherwise, and reported):
  mouser     MOUSER_API_KEY                     Mouser Search API v1 (part number search)
  newark     ELEMENT14_API_KEY                  element14 Product Search API, store www.newark.com
  arrow      ARROW_API_KEY and ARROW_LOGIN      Arrow itemservice v4 (search token)
  tti        TTI_API_KEY                        TTI Search API v1 (keyword search)

A keyed channel's API result replaces what FindChips reported for the same distributor.
"""
import hashlib
import html
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

from .model import Listing, matches, parse_money, parse_qty

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/129.0 Safari/537.36")
DEFAULT_CACHE = os.path.join(os.path.expanduser("~"), ".cache", "opendap", "sourcing")
SECRET_PARAMS = re.compile(r"(?i)(apikey|api_key|callInfo\.apiKey|login)=[^&]*")

# FindChips distributor names mapped onto the matrix channels; the others keep "findchips:<name>".
FINDCHIPS_CHANNELS = {
    "mouser electronics": "mouser",
    "newark": "newark",
    "arrow electronics": "arrow",
    "tti": "tti",
    "tti, inc.": "tti",
    "digi-key": "digikey",
    "digikey": "digikey",
    "lcsc": "lcsc",
    "tme": "tme",
    "future electronics": "future",
    "rs": "rs",
    "farnell": "farnell",
    "avnet": "avnet",
}


# Franchised distributors whose FindChips block does not always carry the marker. FindChips' marker
# is per distributor, not per product line; the matrix says so (docs/specs/sourcing-matrix.md).
KNOWN_FRANCHISED = {"newark", "mouser electronics", "digi-key", "digikey", "arrow electronics", "tti",
                    "tti, inc.", "future electronics", "tme", "rs", "farnell", "avnet"}


def now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class FetchError(Exception):
    pass


class Fetcher:
    """HTTP GET/POST with a polite delay per host and a cache keyed by request.

    The cache keeps every body with the time it was fetched, so a matrix can be rebuilt offline
    from exactly the data it was computed from (--offline), and repeated runs within max_age do
    not hit the channels again."""

    # Seconds between two requests to one host. Digi-Key answers 429 to a faster pace.
    HOST_DELAY = {"www.digikey.com": 8.0}

    def __init__(self, cache_dir=DEFAULT_CACHE, offline=False, max_age_hours=24.0, delay=1.0,
                 timeout=40, retries=2, backoff_429=(60, 180)):
        self.cache_dir = cache_dir
        self.offline = offline
        self.max_age = max_age_hours * 3600.0
        self.delay = delay
        self.timeout = timeout
        self.retries = retries
        self.backoff_429 = backoff_429
        self._last = {}
        self._limited = {}      # host -> consecutive requests that ended rate limited
        self.log = []

    def _key(self, method, url, data):
        h = hashlib.sha256(f"{method} {url} {data or ''}".encode()).hexdigest()[:24]
        return os.path.join(self.cache_dir, h[:2], h)

    def get(self, url, method="GET", data=None, headers=None):
        """Returns (body text, fetched ISO time, final URL)."""
        key = self._key(method, url, data)
        meta_path, body_path = key + ".json", key + ".body"
        if os.path.isfile(meta_path) and os.path.isfile(body_path):
            with open(meta_path) as f:
                meta = json.load(f)
            age = time.time() - meta.get("epoch", 0)
            if self.offline or age <= self.max_age:
                with open(body_path, encoding="utf-8") as f:
                    return f.read(), meta["fetched"], meta.get("final_url", url)
        if self.offline:
            safe = SECRET_PARAMS.sub(r"\1=…", url)
            raise FetchError(f"offline and not cached: {safe}")
        host = urllib.parse.urlparse(url).netloc
        safe = SECRET_PARAMS.sub(r"\1=…", url)
        if self._limited.get(host, 0) >= 2:
            # The host refused twice after backing off: stop asking it during this run.
            raise FetchError(f"{safe}: rate limited (HTTP 429), host not asked again this run")
        wait = self.HOST_DELAY.get(host, self.delay) - (time.time() - self._last.get(host, 0))
        if wait > 0:
            time.sleep(wait)
        hdrs = {"User-Agent": UA, "Accept-Language": "en-US,en;q=0.8"}
        hdrs.update(headers or {})
        body_bytes = data.encode() if isinstance(data, str) else data
        err = None
        text = None
        limited = False
        for attempt in range(self.retries + 1):
            req = urllib.request.Request(url, data=body_bytes, method=method, headers=hdrs)
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as r:
                    text = r.read().decode("utf-8", "replace")
                    final = r.geturl()
                    limited = False
                    break
            except urllib.error.HTTPError as e:
                err = f"HTTP {e.code}"
                limited = e.code == 429
                if e.code not in (429, 500, 502, 503, 504):
                    break
                if limited:
                    ra = parse_qty(e.headers.get("Retry-After")) if e.headers else None
                    if attempt < len(self.backoff_429):
                        time.sleep(ra or self.backoff_429[attempt])
                        continue
                    break
            except (urllib.error.URLError, TimeoutError, OSError) as e:
                err = str(getattr(e, "reason", e))
            time.sleep(2.0 * (attempt + 1))
        self._last[host] = time.time()
        self._limited[host] = self._limited.get(host, 0) + 1 if limited else 0
        if text is None:
            self.log.append({"url": safe, "error": err})
            raise FetchError(f"{safe}: {err}")
        fetched = now_iso()
        os.makedirs(os.path.dirname(key), exist_ok=True)
        with open(body_path, "w", encoding="utf-8") as f:
            f.write(text)
        with open(meta_path, "w") as f:
            json.dump({"url": safe, "final_url": SECRET_PARAMS.sub(r"\1=…", final),
                       "fetched": fetched, "epoch": time.time()}, f)
        self.log.append({"url": safe, "fetched": fetched})
        return text, fetched, final


# ---------------------------------------------------------------- Digi-Key (public pages)

def _next_data(text):
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', text, re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(1))["props"]["pageProps"]["envelope"]["data"]
    except (ValueError, KeyError, TypeError):
        return None


def parse_digikey_listing(text, fetched="", base="https://www.digikey.com"):
    """Rows of a Digi-Key search/filter page. Prices are the tiers the listing shows (often only
    the first three); parse_digikey_detail gives the full table."""
    data = _next_data(text)
    if not data:
        return [], "no product data on the page (layout change or bot check)"
    if "priceQuantity" in data:      # the search went straight to a product page
        one = parse_digikey_detail(text, fetched, base)
        return ([one] if one else []), ""
    out = []
    for row in data.get("products") or []:
        cells = {c.get("analyticsTag"): c.get("value") for c in row if isinstance(c, dict)}
        prod = cells.get("tr-product") or {}
        mpn = prod.get("productNumber")
        if not mpn:
            continue
        qty = None
        for q in cells.get("tr-qtyAvailable") or []:
            qty = (qty or 0) + (parse_qty(q.get("quantity")) or 0)
        breaks = {}
        for u in cells.get("tr-unitPrice") or []:
            for t in u.get("mergedPricingTiers") or []:
                q, p = parse_qty(t.get("brkQty")), parse_money(t.get("unitPrice"))
                if q and p is not None:
                    breaks[q] = min(p, breaks.get(q, p))
        status = (cells.get("tr-productstatus") or {}).get("value", "")
        mfr = ((prod.get("manufacturer") or {}).get("value") or {}).get("label", "")
        compare = cells.get("compare") or {}
        out.append(Listing(
            channel="digikey", seller="Digi-Key", authorized=True, mpn=mpn, manufacturer=mfr,
            stock=qty, breaks=sorted(breaks.items()), lifecycle=status,
            url=base + prod.get("detailUrl", ""), sku=compare.get("productNumber", ""),
            source="digikey page", fetched=fetched))
    return out, ""


def parse_digikey_detail(text, fetched="", base="https://www.digikey.com"):
    data = _next_data(text)
    if not data or "priceQuantity" not in data:
        return None
    ov = data.get("productOverview") or {}
    status = ""
    for a in (data.get("productAttributes") or {}).get("attributes") or []:
        if a.get("label") == "Part Status" and a.get("values"):
            status = a["values"][0].get("value", "")
    stock = None
    for m in data.get("messages") or []:
        mm = re.search(r"In-Stock:\s*([\d,]+)", m.get("message", ""))
        if mm:
            stock = parse_qty(mm.group(1))
    breaks = {}
    packs = set()
    for t in data.get("quantityTable") or []:
        q, p = parse_qty(t.get("breakQty")), parse_money(t.get("unitPrice"))
        packs.add(t.get("packaging", ""))
        if q and p is not None:
            breaks[q] = min(p, breaks.get(q, p))
    if not breaks:
        for pr in (data.get("priceQuantity") or {}).get("pricing") or []:
            packs.add(pr.get("packaging", ""))
            for t in pr.get("mergedPricingTiers") or []:
                q, p = parse_qty(t.get("brkQty")), parse_money(t.get("unitPrice"))
                if q and p is not None:
                    breaks[q] = min(p, breaks.get(q, p))
    notes = []
    if data.get("isBackOrderAllowed") is False and not stock:
        notes.append("no backorders")
    if data.get("isMarketplaceOnly"):
        notes.append("marketplace only")
    numbers = ((ov.get("digikeyProductNumbers") or {}).get("value") or [{}])
    return Listing(
        channel="digikey", seller="Digi-Key", authorized=not data.get("isMarketplaceOnly"),
        mpn=ov.get("manufacturerProductNumber", ""), manufacturer=ov.get("manufacturer", ""),
        stock=stock, breaks=sorted(breaks.items()), lifecycle=status,
        url="", sku=numbers[0].get("value", "") if numbers else "",
        source="digikey page", fetched=fetched,
        note="; ".join(notes + ([f"packaging: {', '.join(sorted(p for p in packs if p))}"] if packs else [])))


def digikey(fetcher, query, patterns):
    url = "https://www.digikey.com/en/products/result?" + urllib.parse.urlencode({"keywords": query})
    text, fetched, final = fetcher.get(url, headers={"Accept": "text/html"})
    rows, problem = parse_digikey_listing(text, fetched)
    if problem:
        raise FetchError(f"digikey: {problem}")
    out = []
    for r in rows:
        if not matches(r.mpn, patterns):
            continue
        if r.url and r.url.startswith("https://www.digikey.com/en/products/detail/"):
            try:
                dtext, dfetched, _ = fetcher.get(r.url, headers={"Accept": "text/html"})
                d = parse_digikey_detail(dtext, dfetched)
                if d:
                    d.url = r.url
                    if d.stock is None:
                        d.stock = r.stock
                    r = d
            except FetchError as e:
                r.note = f"detail page not read ({e}); listing tiers only"
        elif not r.url:
            r.url = final
        out.append(r)
    return out


# ---------------------------------------------------------------- FindChips (aggregator page)

def parse_findchips(text, fetched=""):
    """Every result row on a FindChips search page, grouped under its distributor block."""
    out = []
    blocks = re.split(r'<div id="list\d+" class="distributor-results', text)
    for block in blocks[1:]:
        name_m = re.search(r'data-distributor_name="([^"]*)"', block)
        if not name_m:
            continue
        name = html.unescape(name_m.group(1))
        h2 = re.search(r"<h2 class=\"distributor-title\">(.*?)</h2>", block, re.S)
        authorized = bool(h2 and "Authorized Distributor" in html.unescape(h2.group(1))) \
            or name.lower() in KNOWN_FRANCHISED
        channel = FINDCHIPS_CHANNELS.get(name.lower(), "findchips:" + name)
        for m in re.finditer(r"<tr data-id[^>]*>", block):
            attrs = {k: html.unescape(v) for k, v in re.findall(r'data-([a-z_]+)="([^"]*)"', m.group(0))}
            mpn = attrs.get("mfrpartnumber", "")
            if not mpn:
                continue
            breaks = {}
            try:
                for q, cur, p in json.loads(attrs.get("price") or "[]"):
                    q, p = parse_qty(q), parse_money(p)
                    if q and p is not None and cur == "USD":
                        breaks[q] = min(p, breaks.get(q, p))
            except (ValueError, TypeError):
                pass
            stock = parse_qty(attrs.get("instock") if attrs.get("instock") not in (None, "") else attrs.get("stock"))
            out.append(Listing(
                channel=channel, seller=name, authorized=authorized, mpn=mpn,
                manufacturer=attrs.get("mfr", ""), stock=stock, breaks=sorted(breaks.items()),
                sku=attrs.get("distino", ""), source="findchips", fetched=fetched,
                url=""))
    return out


def findchips(fetcher, query, patterns):
    url = "https://www.findchips.com/search/" + urllib.parse.quote(query)
    text, fetched, _ = fetcher.get(url, headers={"Accept": "text/html"})
    rows = parse_findchips(text, fetched)
    for r in rows:
        r.url = url
    return [r for r in rows if matches(r.mpn, patterns)]


# ---------------------------------------------------------------- LCSC through the JLCPCB parts API

JLC_URL = "https://jlcpcb.com/api/overseas-pcb-order/v1/shoppingCart/smtGood/selectSmtComponentList"


def parse_jlc(text, fetched=""):
    try:
        d = json.loads(text)
        rows = d["data"]["componentPageInfo"]["list"] or []
    except (ValueError, KeyError, TypeError):
        return []
    out = []
    for x in rows:
        breaks = {}
        for t in x.get("componentPrices") or []:
            q, p = parse_qty(t.get("startNumber")), parse_money(t.get("productPrice"))
            if q and p is not None:
                breaks[q] = min(p, breaks.get(q, p))
        stock = parse_qty(x.get("stockCount"))
        out.append(Listing(
            channel="lcsc", seller="LCSC (JLCPCB parts)", authorized=None,
            mpn=x.get("componentModelEn", ""), manufacturer=x.get("componentBrandEn", ""),
            stock=stock, breaks=sorted(breaks.items()), url=x.get("lcscGoodsUrl", ""),
            sku=x.get("componentCode", ""), source="jlcpcb api", fetched=fetched,
            note="" if stock else "no stock: listed price not actionable"))
    return out


def lcsc(fetcher, query, patterns):
    payload = json.dumps({"keyword": query, "currentPage": 1, "pageSize": 100})
    text, fetched, _ = fetcher.get(JLC_URL, method="POST", data=payload,
                                   headers={"Content-Type": "application/json"})
    return [r for r in parse_jlc(text, fetched) if matches(r.mpn, patterns)]


# ---------------------------------------------------------------- keyed APIs (optional)

def parse_mouser_api(text, fetched=""):
    d = json.loads(text)
    out = []
    for p in ((d.get("SearchResults") or {}).get("Parts") or []):
        breaks = {}
        for b in p.get("PriceBreaks") or []:
            q, pr = parse_qty(b.get("Quantity")), parse_money(b.get("Price"))
            if q and pr is not None:
                breaks[q] = min(pr, breaks.get(q, pr))
        stock = parse_qty(p.get("AvailabilityInStock"))
        if stock is None:
            m = re.match(r"\s*([\d,]+)\s+In Stock", p.get("Availability") or "")
            stock = parse_qty(m.group(1)) if m else 0
        out.append(Listing(
            channel="mouser", seller="Mouser Electronics", authorized=True,
            mpn=p.get("ManufacturerPartNumber", ""), manufacturer=p.get("Manufacturer", ""),
            stock=stock, breaks=sorted(breaks.items()), lifecycle=p.get("LifecycleStatus") or "",
            url=p.get("ProductDetailUrl", ""), sku=p.get("MouserPartNumber", ""),
            source="mouser api", fetched=fetched,
            note=f"lead time {p['LeadTime']}" if p.get("LeadTime") else ""))
    return out


def mouser_api(fetcher, query, patterns, key):
    url = "https://api.mouser.com/api/v1/search/partnumber?apiKey=" + urllib.parse.quote(key)
    payload = json.dumps({"SearchByPartRequest": {"mouserPartNumber": query, "partSearchOptions": "None"}})
    text, fetched, _ = fetcher.get(url, method="POST", data=payload,
                                   headers={"Content-Type": "application/json", "Accept": "application/json"})
    return [r for r in parse_mouser_api(text, fetched) if matches(r.mpn, patterns)]


def parse_element14_api(text, fetched=""):
    d = json.loads(text)
    res = d.get("manufacturerPartNumberSearchReturn") or d.get("keywordSearchReturn") or {}
    out = []
    for p in res.get("products") or []:
        breaks = {}
        for b in p.get("prices") or []:
            q, pr = parse_qty(b.get("from")), parse_money(b.get("cost"))
            if q and pr is not None:
                breaks[q] = min(pr, breaks.get(q, pr))
        stock = parse_qty((p.get("stock") or {}).get("level"))
        if stock is None:
            stock = parse_qty(p.get("inv"))
        out.append(Listing(
            channel="newark", seller="Newark", authorized=True,
            mpn=p.get("translatedManufacturerPartNumber", ""), manufacturer=p.get("brandName", ""),
            stock=stock, breaks=sorted(breaks.items()),
            lifecycle=p.get("productStatus") or "", sku=p.get("sku", ""),
            source="element14 api", fetched=fetched))
    return out


def element14_api(fetcher, query, patterns, key):
    params = {
        "term": f"manuPartNum:{query}", "storeInfo.id": "www.newark.com",
        "resultsSettings.offset": "0", "resultsSettings.numberOfResults": "50",
        "resultsSettings.responseGroup": "large", "callInfo.omitXmlSchema": "false",
        "callInfo.responseDataFormat": "json", "callInfo.apiKey": key,
    }
    url = "https://api.element14.com/catalog/products?" + urllib.parse.urlencode(params)
    text, fetched, _ = fetcher.get(url, headers={"Accept": "application/json"})
    return [r for r in parse_element14_api(text, fetched) if matches(r.mpn, patterns)]


def parse_arrow_api(text, fetched=""):
    d = json.loads(text)
    out = []
    for resp in (d.get("itemserviceresult") or {}).get("data") or []:
        for p in resp.get("PartList") or []:
            mpn = p.get("partNum", "")
            mfr = (p.get("manufacturer") or {}).get("mfrName", "")
            status = p.get("status") or ""
            for src in ((p.get("InvOrg") or {}).get("webSites") or []):
                for s in src.get("sources") or []:
                    for sp in s.get("sourceParts") or []:
                        breaks = {}
                        for b in ((sp.get("Prices") or {}).get("resaleList") or []):
                            q, pr = parse_qty(b.get("minQty")), parse_money(b.get("price"))
                            if q and pr is not None:
                                breaks[q] = min(pr, breaks.get(q, pr))
                        stock = 0
                        for a in ((sp.get("Availability") or [])):
                            stock += parse_qty(a.get("fohQty")) or 0
                        out.append(Listing(
                            channel="arrow", seller=f"Arrow ({src.get('code', '')})", authorized=True,
                            mpn=mpn, manufacturer=mfr, stock=stock, breaks=sorted(breaks.items()),
                            lifecycle=status, sku=sp.get("sourcePartId", ""),
                            source="arrow api", fetched=fetched))
    return out


def arrow_api(fetcher, query, patterns, key, login):
    url = "https://api.arrow.com/itemservice/v4/en/search/token?" + urllib.parse.urlencode(
        {"login": login, "apikey": key, "search_token": query, "rows": "50"})
    text, fetched, _ = fetcher.get(url, headers={"Accept": "application/json"})
    return [r for r in parse_arrow_api(text, fetched) if matches(r.mpn, patterns)]


def parse_tti_api(text, fetched=""):
    d = json.loads(text)
    out = []
    for p in d.get("parts") or []:
        breaks = {}
        for b in ((p.get("pricing") or {}).get("quantityPriceBreaks") or []):
            q, pr = parse_qty(b.get("quantity")), parse_money(b.get("price"))
            if q and pr is not None:
                breaks[q] = min(pr, breaks.get(q, pr))
        out.append(Listing(
            channel="tti", seller="TTI", authorized=True, mpn=p.get("manufacturerPartNumber", ""),
            manufacturer=p.get("manufacturer", ""), stock=parse_qty(p.get("availableToSell")),
            breaks=sorted(breaks.items()), lifecycle=p.get("partStatus") or "",
            sku=p.get("ttiPartNumber", ""), url=p.get("buyUrl", ""), source="tti api",
            fetched=fetched, note=f"lead time {p['leadTime']}" if p.get("leadTime") else ""))
    return out


def tti_api(fetcher, query, patterns, key):
    url = "https://api.tti.com/service/api/v1/search/keyword?" + urllib.parse.urlencode({"searchTerms": query})
    text, fetched, _ = fetcher.get(url, headers={"apiKey": key, "Accept": "application/json"})
    return [r for r in parse_tti_api(text, fetched) if matches(r.mpn, patterns)]


# ---------------------------------------------------------------- collection

KEYED = {
    "mouser": ("MOUSER_API_KEY",),
    "newark": ("ELEMENT14_API_KEY",),
    "arrow": ("ARROW_API_KEY", "ARROW_LOGIN"),
    "tti": ("TTI_API_KEY",),
}
ALL_CHANNELS = ("digikey", "mouser", "newark", "arrow", "tti", "lcsc")


def keyed_available(env=os.environ):
    return {ch: all(env.get(v) for v in vs) for ch, vs in KEYED.items()}


def collect(fetcher, query, patterns, env=os.environ, use_findchips=True):
    """All listings for one candidate. Returns (listings, coverage) where coverage says, per
    matrix channel, how it was read: 'page', 'api', 'findchips', 'skipped: …' or 'error: …'."""
    listings, coverage = [], {}
    keyed = keyed_available(env)

    def run(name, how, fn, *args):
        try:
            rows = fn(fetcher, query, patterns, *args)
            coverage[name] = how
            return rows
        except (FetchError, ValueError, KeyError, TypeError) as e:
            coverage[name] = f"error: {e}"
            return []

    listings += run("digikey", "page", digikey)
    listings += run("lcsc", "api (jlcpcb)", lcsc)
    fc = run("findchips", "page", findchips) if use_findchips else []
    api_rows = {}
    if keyed["mouser"]:
        api_rows["mouser"] = run("mouser", "api", mouser_api, env["MOUSER_API_KEY"])
    if keyed["newark"]:
        api_rows["newark"] = run("newark", "api", element14_api, env["ELEMENT14_API_KEY"])
    if keyed["arrow"]:
        api_rows["arrow"] = run("arrow", "api", arrow_api, env["ARROW_API_KEY"], env["ARROW_LOGIN"])
    if keyed["tti"]:
        api_rows["tti"] = run("tti", "api", tti_api, env["TTI_API_KEY"])
    fc_ok = use_findchips and not str(coverage.get("findchips", "")).startswith("error")
    for ch in ("mouser", "newark", "arrow", "tti"):
        if ch in api_rows and not str(coverage.get(ch, "")).startswith("error"):
            listings += api_rows[ch]
            continue
        if fc_ok:
            coverage[ch] = "findchips" + (" (api failed)" if ch in api_rows else "")
        else:
            env_names = " and ".join(KEYED[ch])
            why = coverage.get("findchips", "not used")
            coverage[ch] = f"skipped: no {env_names}; FindChips {why}"
    for r in fc:
        if r.channel == "digikey":       # Digi-Key is read from its own pages
            continue
        if r.channel in api_rows and not str(coverage.get(r.channel, "")).startswith("findchips"):
            continue
        if r.channel == "lcsc":          # LCSC is read from the JLCPCB API
            continue
        listings.append(r)
    coverage.pop("findchips", None)
    return listings, coverage
