"""The sourcing evaluation matrix: candidates in, listings collected, every candidate scored and
explained. Method and file format: docs/specs/sourcing-matrix.md."""
import csv
import io
import re
import json
import os
import tomllib

from .channels import ALL_CHANNELS, collect, now_iso
from .model import LIFECYCLE_RANK, lifecycle_class, matches

# Availability classes, best first (docs/specs/sourcing-matrix.md §Score).
AVAIL = {
    "A": (4, "authorized stock covers the build"),
    "B": (3, "authorized stock below the build"),
    "D": (2, "orderable at an authorized channel, no stock"),
    "C": (1, "LCSC stock only (authorization not verified)"),
    "E": (0, "no authorized listing"),
}
VERDICT_ORDER = {"pass": 0, "conditional": 1, "fail": 2}


class MatrixError(Exception):
    pass


def load(path):
    with open(path, "rb") as f:
        doc = tomllib.load(f)
    if doc.get("version") != 1:
        raise MatrixError(f"{path}: version = 1 expected")
    m = doc.get("matrix") or {}
    cands = doc.get("candidate") or []
    if not cands:
        raise MatrixError(f"{path}: no [[candidate]]")
    seen = set()
    for c in cands:
        if not c.get("mpn"):
            raise MatrixError(f"{path}: a candidate has no mpn")
        if c["mpn"] in seen:
            raise MatrixError(f"{path}: candidate {c['mpn']} is listed twice")
        seen.add(c["mpn"])
        c.setdefault("query", c["mpn"])
        c.setdefault("match", [c["mpn"], c["mpn"] + " TR"])
        c.setdefault("attrs", {})
    for g in doc.get("gate") or []:
        if not g.get("attr") or "pass" not in g:
            raise MatrixError(f"{path}: a [[gate]] needs attr and pass")
    return {
        "title": m.get("title", os.path.basename(path)),
        "build": int(m.get("build_quantity", 1000)),
        "quantities": [int(q) for q in m.get("price_quantities", [1, 100, 1000])],
        "columns": list(m.get("columns", [])),
        "gates": doc.get("gate") or [],
        "candidates": cands,
        "notes": m.get("notes", ""),
    }


def _gate_value(v):
    if v is None:
        return "unknown"
    return re.sub(r"\s*(\[\d+\])+$", "", str(v)).strip() or "empty"


def check_gates(cand, gates):
    """[(verdict, reason)] for every gate."""
    out = []
    for g in gates:
        raw = cand["attrs"].get(g["attr"])
        v = _gate_value(raw)
        passes = [str(x) for x in g.get("pass", [])]
        conds = [str(x) for x in g.get("conditional", [])]
        label = g.get("label", g["attr"])
        if v in passes:
            continue
        shown = raw if raw not in (None, "") else v
        if v in conds or (v == "unknown" and g.get("unknown", "conditional") == "conditional"):
            out.append(("conditional", f"{label} = {shown} ({g.get('why_conditional', 'needs a check')})"))
        else:
            out.append(("fail", f"{label} = {shown} (needs {' or '.join(passes)})"))
    return out


def evaluate(cand, listings, coverage, build, quantities):
    auth = [r for r in listings if r.authorized is True and r.channel != "lcsc"]
    brokers = [r for r in listings if r.authorized is False]
    unknown = [r for r in listings if r.authorized is None and r.channel != "lcsc"]
    lcsc = [r for r in listings if r.channel == "lcsc"]
    auth_stock = sum(r.stock or 0 for r in auth)
    lcsc_stock = sum(r.stock or 0 for r in lcsc)
    broker_stock = sum(r.stock or 0 for r in brokers)
    by_channel = {}
    for r in auth:
        by_channel[r.channel] = by_channel.get(r.channel, 0) + (r.stock or 0)
    if auth_stock >= build:
        avail = "A"
    elif auth_stock > 0:
        avail = "B"
    elif auth:
        avail = "D"
    elif lcsc_stock > 0:
        avail = "C"
    else:
        avail = "E"

    # Lifecycle: the worst statement among the maker's (candidate file) and the channels'.
    statements = []
    if cand.get("lifecycle"):
        statements.append((lifecycle_class(cand["lifecycle"]), cand["lifecycle"],
                           cand.get("lifecycle_source", "candidate file")))
    for r in listings:
        if r.lifecycle:
            statements.append((lifecycle_class(r.lifecycle), r.lifecycle, f"{r.seller}: {r.mpn}"))
    known = [s for s in statements if s[0] != "unknown"]
    life = min(known, key=lambda s: LIFECYCLE_RANK[s[0]])[0] if known else "unknown"

    # Prices. At each quantity: the best authorized offer from a listing with stock, and the best
    # authorized list price whatever the stock. The build price prefers offers whose stock covers
    # the build, then any stocked offer, then list prices (no stock), then LCSC with stock.
    def best_at(pool, q):
        best = None
        for r in pool:
            p = r.price_at(q)
            if p is not None and (best is None or p < best[0]):
                best = (p, r.channel, r.stock or 0)
        return best

    stocked = [r for r in auth if r.stock]
    lcsc_stocked = [r for r in lcsc if r.stock]
    prices = {q: best_at(stocked, q) for q in quantities}
    list_prices = {q: best_at(auth, q) for q in quantities}
    lcsc_prices = {q: best_at(lcsc_stocked, q) for q in quantities}
    build_price = None
    for basis, pool in (("stock covers the build", [r for r in auth if (r.stock or 0) >= build]),
                        ("partial stock", stocked),
                        ("list price, no stock", auth),
                        ("LCSC stock", lcsc_stocked)):
        for r in pool:
            p, bought = r.price_for_build(build)
            if p is not None and (build_price is None or p < build_price[0]):
                build_price = (p, r.channel, bought, basis)
        if build_price:
            break

    reasons = []
    if avail == "A":
        reasons.append(("pass", f"{auth_stock:,} in authorized stock ≥ build of {build:,}"))
    elif avail == "B":
        reasons.append(("conditional", f"only {auth_stock:,} in authorized stock (< build of {build:,})"))
    elif avail == "D":
        chans = sorted({r.channel for r in auth})
        reasons.append(("conditional", f"listed with no stock at {', '.join(chans)} (factory lead time)"))
    elif avail == "C":
        reasons.append(("conditional", f"LCSC stock {lcsc_stock:,} only; no authorized listing"))
    else:
        extra = f"; brokers hold {broker_stock:,}" if broker_stock else ""
        reasons.append(("fail", f"no authorized listing{extra}"))
    if life == "obsolete":
        reasons.append(("fail", "obsolete / end of life: " + "; ".join(
            f"{s[1]} ({s[2]})" for s in known if s[0] == "obsolete")[:200]))
    elif life in ("nrnd", "last-time-buy"):
        reasons.append(("conditional", f"lifecycle {life}: " + "; ".join(
            f"{s[1]} ({s[2]})" for s in known if s[0] == life)[:200]))
    elif life == "unknown":
        reasons.append(("conditional", "lifecycle not stated by any source read"))
    gate_results = check_gates(cand, cand.get("_gates", []))
    reasons = gate_results + reasons
    verdict = "pass"
    for v, _ in reasons:
        if VERDICT_ORDER[v] > VERDICT_ORDER[verdict]:
            verdict = v
    gate_ok = not any(v == "fail" for v, _ in gate_results)
    fetched = sorted({r.fetched for r in listings if r.fetched})
    return {
        "mpn": cand["mpn"],
        "manufacturer": cand.get("manufacturer", ""),
        "attrs": cand["attrs"],
        "query": cand["query"],
        "match": cand["match"],
        "verdict": verdict,
        "gate_ok": gate_ok,
        "reasons": [{"verdict": v, "reason": t} for v, t in reasons],
        "availability": avail,
        "availability_text": AVAIL[avail][1],
        "authorized_stock": auth_stock,
        "authorized_by_channel": by_channel,
        "lcsc_stock": lcsc_stock,
        "broker_stock": broker_stock,
        "unknown_status_stock": sum(r.stock or 0 for r in unknown),
        "lifecycle": life,
        "lifecycle_statements": [{"class": s[0], "text": s[1], "source": s[2]} for s in statements],
        "prices": {str(q): (None if v is None else {"usd": v[0], "channel": v[1], "stock": v[2]})
                   for q, v in prices.items()},
        "list_prices": {str(q): (None if v is None else {"usd": v[0], "channel": v[1], "stock": v[2]})
                        for q, v in list_prices.items()},
        "lcsc_prices": {str(q): (None if v is None else {"usd": v[0], "stock": v[2]})
                        for q, v in lcsc_prices.items()},
        "build_price": None if build_price is None else
        {"usd": build_price[0], "channel": build_price[1], "quantity": build_price[2],
         "basis": build_price[3]},
        "coverage": coverage,
        "fetched": [fetched[0], fetched[-1]] if fetched else [],
        "listings": [r.to_dict() for r in listings],
        "comment": cand.get("comment", ""),
    }


def score(results):
    """Score = availability ×100 + lifecycle ×10 + price points. Price points (0–9) compare the
    build price with the cheapest one among the gate-passing candidates of the same availability
    and lifecycle class (9 = cheapest), so price only orders candidates that tie on the first two.
    Sorted: gate failures last, then by score, then by build price."""
    cheapest = {}
    for r in results:
        if r["gate_ok"] and r["build_price"]:
            k = (r["availability"], r["lifecycle"])
            cheapest[k] = min(cheapest.get(k, float("inf")), r["build_price"]["usd"])
    for r in results:
        pp = 0
        ref = cheapest.get((r["availability"], r["lifecycle"]))
        if ref and r["build_price"] and r["build_price"]["usd"] > 0:
            pp = min(9, int(round(9 * ref / r["build_price"]["usd"])))
        r["score_parts"] = {"availability": AVAIL[r["availability"]][0],
                            "lifecycle": LIFECYCLE_RANK[r["lifecycle"]], "price": pp}
        r["score"] = AVAIL[r["availability"]][0] * 100 + LIFECYCLE_RANK[r["lifecycle"]] * 10 + pp
    results.sort(key=lambda r: (not r["gate_ok"], -r["score"],
                                r["build_price"]["usd"] if r["build_price"] else float("inf"), r["mpn"]))
    for i, r in enumerate(results, 1):
        r["rank"] = i
    return results


def run(spec, fetcher, env=os.environ, progress=None, use_findchips=True):
    """Collect once per query (with the union of the match patterns of the candidates that share
    it), then evaluate each candidate on the rows its own patterns match."""
    patterns = {}
    for c in spec["candidates"]:
        patterns.setdefault(c["query"], []).extend(c["match"])
    cache = {}
    results = []
    for c in spec["candidates"]:
        c["_gates"] = spec["gates"]
        if c["query"] not in cache:
            cache[c["query"]] = collect(fetcher, c["query"], patterns[c["query"]], env=env,
                                        use_findchips=use_findchips)
        all_rows, coverage = cache[c["query"]]
        rows = [r for r in all_rows if matches(r.mpn, c["match"])]
        results.append(evaluate(c, rows, dict(coverage), spec["build"], spec["quantities"]))
        if progress:
            progress(c["mpn"], results[-1])
    return score(results)


# ---------------------------------------------------------------- output

def _money(v):
    return "" if v is None else f"{v['usd']:,.2f}"


def _stock_text(r):
    parts = [f"{ch} {n:,}" for ch, n in sorted(r["authorized_by_channel"].items())]
    return ", ".join(parts) if parts else "0"


def write_csv(results, spec, path):
    cols = spec["columns"]
    q = spec["quantities"]
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["rank", "mpn", "manufacturer", *cols, "verdict", "score", "availability",
                    "authorized_stock", "authorized_by_channel", "lcsc_stock", "broker_stock",
                    "lifecycle", *[f"auth_usd_at_{n}" for n in q], *[f"auth_channel_at_{n}" for n in q],
                    *[f"list_usd_at_{n}" for n in q], *[f"lcsc_usd_at_{n}" for n in q],
                    "build_usd", "build_channel", "build_qty", "build_basis",
                    "fetched_first", "fetched_last", "reasons"])
        for r in results:
            bp = r["build_price"] or {}
            w.writerow([r["rank"], r["mpn"], r["manufacturer"], *[r["attrs"].get(c, "") for c in cols],
                        r["verdict"], r["score"], r["availability"], r["authorized_stock"],
                        "; ".join(f"{k}={v}" for k, v in sorted(r["authorized_by_channel"].items())),
                        r["lcsc_stock"], r["broker_stock"], r["lifecycle"],
                        *[(r["prices"][str(n)] or {}).get("usd", "") for n in q],
                        *[(r["prices"][str(n)] or {}).get("channel", "") for n in q],
                        *[(r["list_prices"][str(n)] or {}).get("usd", "") for n in q],
                        *[(r["lcsc_prices"][str(n)] or {}).get("usd", "") for n in q],
                        bp.get("usd", ""), bp.get("channel", ""), bp.get("quantity", ""), bp.get("basis", ""),
                        *(r["fetched"] or ["", ""]),
                        " | ".join(f"{x['verdict']}: {x['reason']}" for x in r["reasons"])])


def write_listings_csv(results, path):
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["candidate", "channel", "seller", "authorized", "mpn", "manufacturer", "stock",
                    "price_breaks_usd", "lifecycle", "sku", "url", "source", "fetched", "note"])
        for r in results:
            for x in r["listings"]:
                w.writerow([r["mpn"], x["channel"], x["seller"],
                            {True: "yes", False: "no", None: "not stated"}[x["authorized"]],
                            x["mpn"], x["manufacturer"], "" if x["stock"] is None else x["stock"],
                            " ".join(f"{q}@{p:g}" for q, p in x["breaks"]), x["lifecycle"], x["sku"],
                            x["url"], x["source"], x["fetched"], x["note"]])


_URL = re.compile(r"(?<!<)(?<!\]\()https?://[^\s<>|\\]+")


def _autolink(m):
    """<url>, leaving out trailing punctuation and a closing parenthesis the URL did not open."""
    url, tail = m.group(0), ""
    while url and (url[-1] in ".,;:" or (url[-1] == ")" and url.count(")") > url.count("("))):
        url, tail = url[:-1], url[-1] + tail
    return f"<{url}>{tail}"


def _cell(s):
    """A table or list cell: pipes escaped, one line, URLs as autolinks (no bare URLs)."""
    return _URL.sub(_autolink, str(s).replace("|", "\\|").replace("\n", " "))


def markdown(results, spec, generated=None, level=2):
    """The matrix as Markdown: one ranked table, then the reasons and the channel coverage."""
    cols = spec["columns"]
    q = spec["quantities"]
    out = io.StringIO()
    p = out.write
    p(f"{'#' * level} Matrix\n\nGenerated by `odapkit parts matrix` on {generated or now_iso()}; build quantity "
      f"{spec['build']:,}. Prices in USD: \"$ @n\" is the best authorized offer from a listing "
      f"with stock (blank: no stocked offer, or n below its minimum order); the build price "
      f"says its basis when stock does not cover the build.\n\n")
    head = ["#", "Part", "Maker", *cols, "Verdict", "Score", "Authorized stock", "LCSC", "Brokers",
            "Lifecycle", *[f"$ @{n:,}" for n in q], f"$ for {spec['build']:,}"]
    p("| " + " | ".join(head) + " |\n")
    p("|" + "|".join("---" if i < 3 + len(cols) else "---:" if h.startswith("$") or h in (
        "Score", "LCSC", "Brokers") else "---" for i, h in enumerate(head)) + "|\n")
    for r in results:
        bp = r["build_price"]
        btxt = "" if not bp else f"{bp['usd']:,.2f} {bp['channel']}" + (
            f", MOQ {bp['quantity']:,}" if bp["quantity"] != spec["build"] else "") + (
            "" if bp["basis"] == "stock covers the build" else f" ({bp['basis']})")
        row = [r["rank"], f"{r['mpn']}", r["manufacturer"], *[r["attrs"].get(c, "") for c in cols],
               f"**{r['verdict']}**" if r["verdict"] == "pass" else r["verdict"], r["score"],
               _stock_text(r), f"{r['lcsc_stock']:,}", f"{r['broker_stock']:,}", r["lifecycle"],
               *[_money(r["prices"][str(n)]) for n in q], btxt]
        p("| " + " | ".join(_cell(x) for x in row) + " |\n")
    h = "#" * level
    p(f"\n{h} Why each candidate passes or fails\n\n")
    for r in results:
        why = "; ".join(f"{x['verdict']}: {x['reason']}" for x in r["reasons"])
        extra = f" {r['comment']}" if r.get("comment") else ""
        p(f"- **{r['rank']}. {_cell(r['mpn'])}** ({r['verdict']}, {r['score']}): {_cell(why)}.{_cell(extra)}\n")
    p(f"\n{h} Channel coverage\n\nHow each channel was read for each query.\n\n")
    seen = {}
    for r in results:
        seen.setdefault(r["query"], r["coverage"])
    chans = list(ALL_CHANNELS)
    p("| Query | " + " | ".join(chans) + " |\n")
    p("|---|" + "|".join("---" for _ in chans) + "|\n")
    for query, cov in seen.items():
        p(f"| {_cell(query)} | " + " | ".join(_cell(cov.get(ch, "")) for ch in chans) + " |\n")
    return out.getvalue()


SCHEMA = "opendap.sourcing-matrix/v1"


def render_all(results, spec, outdir, generated=None):
    """matrix.md, matrix.csv and listings.csv in outdir; returns the Markdown matrix."""
    os.makedirs(outdir, exist_ok=True)
    write_csv(results, spec, os.path.join(outdir, "matrix.csv"))
    write_listings_csv(results, os.path.join(outdir, "listings.csv"))
    md = markdown(results, spec, generated)
    with open(os.path.join(outdir, "matrix.md"), "w") as f:
        f.write(f"# {spec['title']}\n\n" + md)
    return md


def document(results, spec, generated=None):
    """matrix.json: everything, with the spec's column list so the matrix can be re-rendered."""
    return {"schema": SCHEMA, "title": spec["title"], "generated": generated or now_iso(),
            "build_quantity": spec["build"], "price_quantities": spec["quantities"],
            "columns": spec["columns"], "results": results}


def write_all(results, spec, outdir, generated=None):
    generated = generated or now_iso()
    md = render_all(results, spec, outdir, generated)
    with open(os.path.join(outdir, "matrix.json"), "w") as f:
        json.dump(document(results, spec, generated), f, indent=1, ensure_ascii=False)
    return md


def load_saved(path, columns=None):
    """A saved matrix.json as (document, spec), for render_all. `columns` supplies the spec's
    column list for a document that does not record it, or replaces the recorded one."""
    with open(path, encoding="utf-8") as f:
        try:
            doc = json.load(f)
        except json.JSONDecodeError as e:
            raise MatrixError(f"{path}: not JSON ({e})") from e
    if not isinstance(doc, dict) or doc.get("schema") != SCHEMA:
        raise MatrixError(f"{path}: schema {SCHEMA} expected")
    for key in ("title", "build_quantity", "price_quantities", "results"):
        if key not in doc:
            raise MatrixError(f"{path}: no {key}")
    cols = list(columns) if columns is not None else doc.get("columns")
    if cols is None:
        raise MatrixError(f"{path}: no columns recorded; pass --columns a,b,c in the candidates "
                          "file's order (the attribute columns of the matrix.md header)")
    known = {k for r in doc["results"] for k in r.get("attrs", {})}
    unknown = [c for c in cols if c not in known]
    if unknown and doc["results"]:
        raise MatrixError(f"{path}: no candidate has the attribute {', '.join(unknown)}")
    doc["columns"] = cols
    spec = {"title": doc["title"], "build": int(doc["build_quantity"]),
            "quantities": [int(q) for q in doc["price_quantities"]], "columns": cols}
    return doc, spec
