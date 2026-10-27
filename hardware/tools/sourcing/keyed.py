"""Keyed channel adapters (optional; read only when their variables are set, see channels.py): the Mouser,
element14 (Newark), Arrow and TTI APIs, each a parser of its JSON answer and a fetch through the shared Fetcher.
channels.py collects them with the keyless channels."""
import json
import re
import urllib.parse

from .model import Listing, matches, parse_money, parse_qty


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
