"""Read Rockchip's "DDR SDRAM Support List" PDF into rows, with one SoC's status per row.

The list is an Excel export: one table per maker group, the maker name in a merged cell centred
on its rows, one column per SoC family. pdftotext -bbox (poppler) gives every word with its box;
this module rebuilds the rows from those boxes:

- a row is anchored on its package word ("200ball"); the other columns are read by x position
  (part number, product status, density, organisation, type);
- the SoC column is found by its header word (for example RK3566) on each page, and a status
  word (√, T/A, S/A, N/A) belongs to it when its centre is within half a column of the header's
  centre; note marks such as [7][11] that follow the status are kept with it;
- the maker of each row is the merged-cell label: rows and labels are partitioned into
  contiguous blocks (dynamic programming) so that each label sits at the middle of its block;
- the section (Consumer, Industrial&Wide Temperature, Automotive) is the last one seen.

Pages without the SoC's column (for example the automotive page, which has RK3568M but no RK3566)
give rows with status "not in this table".
"""
import html
import re
import shutil
import subprocess

STATUS = re.compile(r"^(√|T/A|S/A|N/A)$")
NOTE = re.compile(r"^(\[\d+\])+$")
SECTIONS = ("Consumer", "Industrial&Wide", "Automotive")
DENSITY = re.compile(r"(\d+)G\s*bit")

# Column x ranges (pt) of the Ver 2.x layout, before the SoC columns.
COLS = {"part": (75, 186), "status": (155, 186), "density": (185, 211), "org": (210, 235),
        "type": (232, 272)}


def bbox_html(pdf):
    """The pdftotext -bbox output of a PDF (needs poppler's pdftotext)."""
    exe = shutil.which("pdftotext")
    if not exe:
        raise RuntimeError("pdftotext not found (poppler)")
    r = subprocess.run([exe, "-bbox", pdf, "-"], capture_output=True, text=True, check=True)
    return r.stdout


def _words(page):
    out = []
    for a, b, c, d, t in re.findall(
            r'xMin="([\d.]+)" yMin="([\d.]+)" xMax="([\d.]+)" yMax="([\d.]+)">([^<]*)<', page):
        out.append((float(a), float(b), float(c), float(d), html.unescape(t)))
    return out


def _labels(words, top):
    """Merged maker labels in the first column, multi-word labels joined."""
    cand = sorted([w for w in words if w[2] < 76 and w[1] > top], key=lambda w: (w[1], w[0]))
    groups = []
    for w in cand:
        for g in groups:
            if abs(g["y"] - w[1]) < 7.5 and not (w[0] > g["x1"] + 4 or w[2] < g["x0"] - 40):
                g["words"].append(w)
                g["x0"], g["x1"] = min(g["x0"], w[0]), max(g["x1"], w[2])
                break
        else:
            groups.append({"y": w[1], "x0": w[0], "x1": w[2], "words": [w]})
    out = []
    for g in groups:
        ws = sorted(g["words"], key=lambda w: (round(w[1]), w[0]))
        y = sum((w[1] + w[3]) / 2 for w in ws) / len(ws)
        out.append((y, " ".join(w[4] for w in ws)))
    return sorted(out)


def _assign(rows_y, labels):
    """Partition rows (sorted y) into len(labels) contiguous blocks minimising the distance of
    each label to the middle of its block. Returns the label index of each row."""
    n, m = len(rows_y), len(labels)
    if m == 0 or n == 0:
        return [None] * n, labels
    if m > n:
        # More labels than rows on this page: keep the nearest labels only.
        labels = sorted(labels, key=lambda lab: min(abs(lab[0] - y) for y in rows_y))[:n]
        labels.sort()
        m = n
    INF = float("inf")
    cost = [[INF] * (n + 1) for _ in range(m + 1)]
    back = [[0] * (n + 1) for _ in range(m + 1)]
    cost[0][0] = 0.0
    for j in range(1, m + 1):
        ly = labels[j - 1][0]
        for i in range(j, n - (m - j) + 1):
            for k in range(j - 1, i):
                if cost[j - 1][k] == INF:
                    continue
                mid = (rows_y[k] + rows_y[i - 1]) / 2
                c = cost[j - 1][k] + abs(ly - mid)
                if c < cost[j][i]:
                    cost[j][i], back[j][i] = c, k
    idx = [None] * n
    i = n
    for j in range(m, 0, -1):
        k = back[j][i]
        for r in range(k, i):
            idx[r] = j - 1
        i = k
    return [labels[k][1] if k is not None else None for k in idx], labels


def parse(bbox, soc):
    """Every table row of the list, with `soc`'s cell. Returns a list of dicts."""
    pages = bbox.split("<page ")[1:]
    rows = []
    section = ""
    for pno, page in enumerate(pages, 1):
        words = _words(page)
        anchors = sorted([w for w in words if re.fullmatch(r"\d+ball", w[4])], key=lambda w: w[1])
        if not anchors:
            continue
        top = anchors[0][1] - 3
        for w in words:
            if w[1] < top and w[4] in SECTIONS:
                section = "Industrial&Wide Temperature" if w[4] == "Industrial&Wide" else w[4]
        header = [w for w in words if w[1] < top and soc.upper() in [p.upper() for p in w[4].split("/") if p]]
        socx = None
        if header:
            h = header[0]
            socx = (h[0] + h[2]) / 2
            others = sorted({round((w[0] + w[2]) / 2, 1) for w in words
                             if w[1] < top and re.match(r"^(RK|RV|PX)", w[4]) and w not in header})
            gaps = [abs(x - socx) for x in others if abs(x - socx) > 2]
            half = (min(gaps) / 2) if gaps else 8.0
        labels = _labels(words, top)
        page_rows = []
        for a in anchors:
            y0, y1 = a[1], a[3]
            yc = (y0 + y1) / 2
            band = [w for w in words if abs((w[1] + w[3]) / 2 - yc) < 1.5]
            # A part number may wrap onto two lines centred on the row (SPECTEK): widen only then.
            near = [w for w in words if abs((w[1] + w[3]) / 2 - yc) < 4.5]

            def col(name, ws):
                lo, hi = COLS[name]
                sel = [w for w in ws if lo <= w[0] < hi]
                if name == "part":
                    sel = [w for w in sel if not (COLS["status"][0] <= w[0])]
                return " ".join(w[4] for w in sorted(sel, key=lambda w: (round(w[1]), w[0])))

            part = col("part", band) or col("part", near)
            part_clean = re.sub(r"\s*[（(][^)）]*[)）]\s*$", "", part).strip()
            part_clean = re.sub(r"\s+-\s*", "-", re.sub(r"\s*-\s+", "-", part_clean))
            cell = ""
            if socx is not None:
                sts = sorted([w for w in band if w[0] > 286 and STATUS.match(w[4])
                              and abs((w[0] + w[2]) / 2 - socx) < half], key=lambda w: w[0])
                if sts:
                    last = sts[-1]
                    cell = " ".join(w[4] for w in sts)
                    for w in sorted(band, key=lambda w: w[0]):
                        if NOTE.match(w[4]) and 0 <= w[0] - last[2] < 4:
                            cell += " " + w[4]
                            last = w
                else:
                    cell = ""
            dens = col("density", band)
            dm = DENSITY.search(dens)
            page_rows.append({
                "page": pno, "section": section, "y": (y0 + y1) / 2,
                "part": part_clean, "part_raw": part,
                "list_status": col("status", band), "density": dens,
                "density_gbit": int(dm.group(1)) if dm else None,
                "organization": col("org", band), "type": col("type", band), "package": a[4],
                "soc": soc, "soc_status": (cell or "") if socx is not None else "not in this table",
            })
        makers, _ = _assign([r["y"] for r in page_rows], labels) if labels else ([None] * len(page_rows), [])
        for r, mk in zip(page_rows, makers):
            r["maker"] = mk or ""
            del r["y"]
        rows += page_rows
    return rows


def status_class(cell):
    """'√', 'T/A', 'S/A', 'N/A', '' (not evaluated) or 'not in this table', notes removed."""
    return re.sub(r"\s*(\[\d+\])+$", "", cell or "").strip()


def notes(cell):
    return re.findall(r"\[(\d+)\]", cell or "")


def search_terms(part):
    """(query, match patterns) for a list entry. Wildcards (**, *) become glob patterns, the
    query is the part before the first wildcard; a grade after a space (Micron "-046 IT:C") is
    matched but not searched, since channels index the base number. Micron's FBGA code is
    already removed by parse(). An entry without a second part matches every suffix."""
    if "*" in part:
        q = part.split("*")[0].rstrip("-")
        return q, [part.replace("**", "*")]
    if "-" not in part and " " not in part:
        # No second part at all (SK hynix, Nanya, Kingston): the list names the first part
        # only, which "supports all the chips that match the first part" (list p. 3).
        return part, [part + "*"]
    return part.split(" ")[0], [part, part + " TR"]


def derived(row):
    """Attributes the list states indirectly: LPDDR4X mode (note [11] limits a unified part to
    LPDDR4 mode on that SoC) and two chip selects ("/2cs" in the density cell)."""
    out = {}
    t = row["type"].upper()
    if "LPDDR4X" in t and "11" not in notes(row["soc_status"]):
        out["lpddr4x_mode"] = "yes"
    else:
        out["lpddr4x_mode"] = "LPDDR4 only"
    if "2cs" in row["density"].lower():
        out["ranks"] = 2
    return out
