"""Shared model: one distributor listing of one part, MPN matching and price lookup."""
import fnmatch
import re
from dataclasses import asdict, dataclass, field

# Lifecycle classes, best first. Distributor and maker wording is mapped onto them.
LIFECYCLE_RANK = {"active": 3, "unknown": 2, "nrnd": 1, "last-time-buy": 1, "obsolete": 0}
_LIFECYCLE_WORDS = [
    (r"last\s*time\s*buy|\bltb\b", "last-time-buy"),
    (r"not\s*recommended|\bnrnd\b|not\s*for\s*new\s*designs", "nrnd"),
    (r"obsolete|\beol\b|end\s*of\s*life|discontinued", "obsolete"),
    (r"\bactive\b|in\s*production|new\s*product|preliminary|mass\s*production", "active"),
]


def lifecycle_class(text):
    """Map a lifecycle statement onto one of LIFECYCLE_RANK's classes ("unknown" when empty).

    "Discontinued at Digi-Key" is the distributor dropping the line, not the maker: it maps to
    "nrnd", so it lowers the rank without being read as a maker end of life."""
    if not text:
        return "unknown"
    t = str(text).strip().lower()
    if "discontinued at" in t:
        return "nrnd"
    for pattern, cls in _LIFECYCLE_WORDS:
        if re.search(pattern, t):
            return cls
    return "unknown"


def norm(mpn):
    """Upper case, letters and digits only: 'MT53E1G32D2FW-046 IT:C' -> 'MT53E1G32D2FW046ITC'."""
    return re.sub(r"[^A-Z0-9*?]", "", str(mpn).upper())


def matches(mpn, patterns):
    """True when the normalised MPN matches one of the normalised glob patterns."""
    n = norm(mpn)
    return any(fnmatch.fnmatchcase(n, norm(p)) for p in patterns)


def parse_money(text):
    if text is None:
        return None
    if isinstance(text, (int, float)):
        return float(text)
    t = re.sub(r"[^0-9.]", "", str(text).replace(",", ""))
    try:
        return float(t) if t else None
    except ValueError:
        return None


def parse_qty(text):
    if text is None:
        return None
    if isinstance(text, int):
        return text
    t = re.sub(r"[^0-9]", "", str(text))
    return int(t) if t else None


@dataclass
class Listing:
    """One part number at one channel, as read on `fetched` (UTC, ISO 8601)."""

    channel: str              # digikey, mouser, newark, arrow, tti, lcsc, or findchips:<name>
    seller: str               # the distributor's own name
    authorized: bool | None   # True franchised/authorized, False broker, None not stated
    mpn: str                  # the maker part number as the channel lists it
    manufacturer: str = ""
    stock: int | None = None  # None: the channel does not say
    breaks: list = field(default_factory=list)   # [(qty, unit price USD)], ascending qty
    currency: str = "USD"
    lifecycle: str = ""       # the channel's wording, verbatim
    url: str = ""
    sku: str = ""
    source: str = ""          # how it was read: page, api, aggregator
    fetched: str = ""
    note: str = ""

    def moq(self):
        return self.breaks[0][0] if self.breaks else None

    def price_at(self, qty):
        """Unit price for an order of `qty`: the highest break at or below it; None below the MOQ."""
        best = None
        for q, p in self.breaks:
            if q <= qty:
                best = p
        return best

    def price_for_build(self, qty):
        """Unit price to buy at least `qty`: the break for qty, or the MOQ break when the MOQ is
        larger. Returns (price, quantity bought)."""
        p = self.price_at(qty)
        if p is not None:
            return p, qty
        if self.breaks:
            return self.breaks[0][1], self.breaks[0][0]
        return None, None

    def to_dict(self):
        d = asdict(self)
        d["breaks"] = [[q, p] for q, p in self.breaks]
        return d
