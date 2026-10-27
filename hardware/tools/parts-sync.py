#!/usr/bin/env python3
"""Fetch the external part files listed in libs/parts/manifest.toml into the local cache
(~/.cache/opendap/parts/<manufacturer>/<mpn>/) and verify their sha256.

  parts-sync.py [MANIFEST] [--json] [--mpn MPN]...

--mpn limits the run to the entries of those parts (repeatable).

Files without a sha256 in the manifest are downloaded and their hash is printed, so it can be
recorded. Empty downloads, and datasheets that are not PDFs (a blocked or moved page), are
deleted and reported as errors. Entries with an empty url (no public file) are reported as "no-public-file". Exit code: 0 all verified, 1 a hash mismatch or download failure.
"""
import hashlib, json, os, re, sys, tomllib, urllib.request

def main(argv):
    as_json = "--json" in argv
    mpns, args, it = set(), [], iter(argv)
    for a in it:
        if a == "--mpn":
            mpns.add(next(it, ""))
        elif not a.startswith("--"):
            args.append(a)
    here = os.path.dirname(os.path.abspath(__file__))
    manifest = args[0] if args else os.path.join(here, "..", "..", "..", "libs", "parts", "manifest.toml")
    files = tomllib.load(open(manifest, "rb")).get("file", [])
    if mpns:
        files = [f for f in files if f.get("mpn") in mpns]
    cache = os.path.join(os.environ.get("XDG_CACHE_HOME", os.path.expanduser("~/.cache")), "opendap", "parts")
    results, ok = [], True
    for f in files:
        if not f.get("url"):
            results.append({"mpn": f["mpn"], "kind": f["kind"], "status": "no-public-file"}); continue
        slug = lambda s: re.sub(r"[^A-Za-z0-9._-]+", "-", s)
        d = os.path.join(cache, slug(f["manufacturer"]), slug(f["mpn"]))
        os.makedirs(d, exist_ok=True)
        name = os.path.basename(f["url"].split("?")[0]) or f"{f['kind']}.bin"
        path = os.path.join(d, name)
        status = "cached"
        if not os.path.exists(path):
            try:
                req = urllib.request.Request(f["url"], headers={"User-Agent": "opendap-parts-sync"})
                with urllib.request.urlopen(req, timeout=120) as r, open(path, "wb") as out:
                    out.write(r.read())
                status = "downloaded"
            except Exception as e:
                ok = False; results.append({"mpn": f["mpn"], "kind": f["kind"], "status": "error", "error": str(e)}); continue
        data = open(path, "rb").read()
        if not data or (f["kind"] == "datasheet" and not data.startswith(b"%PDF-")):
            os.remove(path); ok = False
            results.append({"mpn": f["mpn"], "kind": f["kind"], "status": "error", "error": "empty or not a PDF (blocked or moved page)"}); continue
        digest = hashlib.sha256(data).hexdigest()
        want = f.get("sha256")
        if want and want != digest:
            ok = False; status = "hash-mismatch"
        results.append({"mpn": f["mpn"], "kind": f["kind"], "status": status, "path": path, "sha256": digest})
    if as_json:
        print(json.dumps({"schema": "opendap.parts-sync/v1", "items": results}))
    else:
        for r in results:
            print(f"{r['status']:15} {r['mpn']:28} {r['kind']:10} {r.get('sha256', r.get('error', ''))}")
    return 0 if ok else 1

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
