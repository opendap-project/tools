"""What odw render does to a program's file so the same sources give the same bytes (RFC 0021 §5).

- `pdf(data)`: the PDF `CreationDate` and `ModDate` rewritten to a fixed date of the same length, so the
  cross-reference offsets hold.
- `svg(data)`: the creation date dropped from the title, and the `<path>` elements of each stroke-font glyph
  group (`<g class="stroked-text">`, open strokes with no area) merged into one path of several subpaths, which
  draws the same.
- `png(data)`: the ancillary chunks that carry time or text (`tIME`, `tEXt`, `zTXt`, `iTXt`) dropped.
- `png_close(a, b)`: two PNGs of one size that differ in at most `PNG_PIXELS` of their pixels, by at most
  `PNG_CHANNEL` in a channel (KiCad 10.0.7's 3D renders differ run to run in about 0.002 % of the pixels).
Standard library only.
"""
import re
import struct
import zlib

FIXED_DATE = "20000101000000"
PNG_PIXELS = 0.001      # share of the pixels that may differ
PNG_CHANNEL = 32        # largest difference in one channel (RFC 0021 §5.4; measured runs differ by up to 17)
_DROP = {b"tIME", b"tEXt", b"zTXt", b"iTXt"}


def read_bytes(path):
    with open(path, "rb") as fh:
        return fh.read()


def _same_length_date(m):
    digits = iter(FIXED_DATE * 2)
    return m.group(1) + re.sub(rb"[0-9]", lambda _: next(digits).encode(), m.group(2)) + b")"


def pdf(data: bytes) -> bytes:
    return re.sub(rb"(/(?:CreationDate|ModDate) \()([^)]*)\)", _same_length_date, data)


_GLYPHS = re.compile(r'(<g class="stroked-text">(?:<desc>[^<]*</desc>)?\s*)((?:<path d="[^"]*"\s*/>\s*){2,})(</g>)')


def _merge(m):
    ds = re.findall(r'<path d="([^"]*)"', m.group(2))
    return m.group(1) + '<path d="' + "".join(" ".join(d.split()) for d in ds) + '" />\n' + m.group(3)


def svg(data: bytes) -> bytes:
    text = data.decode("utf-8")
    text = re.sub(r"(<title>SVG Image created as [^<]*?) date [0-9T:\-]+ *(</title>)", r"\1\2", text)
    return _GLYPHS.sub(_merge, text).encode("utf-8")


def _chunks(data):
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("not a PNG file")
    i = 8
    while i < len(data):
        n = struct.unpack(">I", data[i:i + 4])[0]
        yield data[i + 4:i + 8], data[i:i + 12 + n], data[i + 8:i + 8 + n]
        i += 12 + n


def png(data: bytes) -> bytes:
    return data[:8] + b"".join(raw for kind, raw, _ in _chunks(data) if kind not in _DROP)


def _pixels(data):
    hdr, idat = None, b""
    for kind, _, body in _chunks(data):
        if kind == b"IHDR":
            hdr = struct.unpack(">IIBBBBB", body)
        elif kind == b"IDAT":
            idat += body
    w, h, depth, colour = hdr[:4]
    if depth != 8 or colour not in (0, 2, 4, 6):
        raise ValueError("only 8-bit grey, RGB and RGBA PNGs are compared")
    bpp = {0: 1, 2: 3, 4: 2, 6: 4}[colour]
    raw, stride, rows, prev, k = zlib.decompress(idat), w * bpp, [], bytearray(w * bpp), 0
    for _ in range(h):
        f, line = raw[k], bytearray(raw[k + 1:k + 1 + stride])
        k += 1 + stride
        _unfilter(f, line, prev, bpp)
        rows.append(bytes(line))
        prev = line
    return w, h, bpp, rows


def _unfilter(f, line, prev, bpp):
    for x in range(len(line)):
        a = line[x - bpp] if x >= bpp else 0
        b = prev[x]
        c = prev[x - bpp] if x >= bpp else 0
        if f == 1:
            line[x] = (line[x] + a) & 255
        elif f == 2:
            line[x] = (line[x] + b) & 255
        elif f == 3:
            line[x] = (line[x] + ((a + b) >> 1)) & 255
        elif f == 4:
            pa, pb, pc = abs(b - c), abs(a - c), abs(a + b - 2 * c)
            line[x] = (line[x] + (a if pa <= pb and pa <= pc else b if pb <= pc else c)) & 255


def png_close(a: bytes, b: bytes) -> bool:
    if a == b:
        return True
    try:
        wa, ha, pa, ra = _pixels(a)
        wb, hb, pb, rb = _pixels(b)
    except (ValueError, zlib.error, struct.error):
        return False
    if (wa, ha, pa) != (wb, hb, pb):
        return False
    allowed, n = int(PNG_PIXELS * wa * ha), 0
    for la, lb in zip(ra, rb):
        if la == lb:
            continue
        for x in range(0, len(la), pa):
            d = max(abs(la[x + j] - lb[x + j]) for j in range(pa))
            if d > PNG_CHANNEL:
                return False
            if d:
                n += 1
                if n > allowed:
                    return False
    return True
