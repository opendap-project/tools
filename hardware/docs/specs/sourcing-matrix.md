# Sourcing matrix

`odapkit parts matrix` turns a list of candidate parts into an evaluation matrix: for every candidate and every channel, the stock, the price breaks, the lifecycle status and the date they were read; then a verdict and a score, with the reason each candidate passes or fails. It serves requirements such as T1-010, T1-028 and T1-029 (prefer available parts, validate availability before adoption).

The tool and this document are public; its inputs and outputs are not. Stock, prices and the matrices built from them are sourcing data: they are never committed to a public repository, so `--out` is required and should point to a private location.

## Candidates file

TOML, one file per question (for example "which memory part for a board").

```toml
version = 1

[matrix]
title = "Example matrix"
build_quantity = 1000              # the order size stock and price are judged at (default 1000)
price_quantities = [1, 100, 1000]  # the price columns
columns = ["density_gbit", "rk3566"]   # attributes shown in the matrix

[[gate]]                           # compatibility checks on the candidates' attributes
attr = "rk3566"
label = "RK3566 (Rockchip list)"   # optional
pass = ["√", "T/A"]
conditional = ["S/A"]              # optional
why_conditional = "…"              # optional, printed with the reason
unknown = "fail"                   # missing attribute: "conditional" (default) or "fail"

[[candidate]]
mpn = "XM4X1G32-046 IT:C"          # shown in the matrix; unique (example part number)
manufacturer = "Example"
query = "XM4X1G32-046"             # what the channels are searched for (default: mpn)
match = ["XM4X1G32-046 IT:C", "XM4X1G32-046 IT:C TR"]   # default: mpn and mpn + " TR"
lifecycle = "discontinued"         # optional: a maker statement the channels may not carry
lifecycle_source = "maker page, read <date>"
comment = "…"                      # optional, printed with the reasons
[candidate.attrs]
density_gbit = 32
rk3566 = "T/A"
```

`match` patterns compare normalised part numbers (upper case, letters and digits only), with `*` and `?` as wildcards, so `XM4X1G32-046-IT-C-TR` matches `XM4X1G32-046 IT:C TR`. Gate values are compared as text after removing trailing note marks such as `[7][11]`.

`odapkit parts rk-ddr-list LIST.pdf --soc RK3566 … --format toml` writes candidate stubs from Rockchip's DDR SDRAM Support List: each row's SoC status, density, type, package and the list's product-status column (as `lifecycle`), plus two attributes the list states indirectly: `lpddr4x_mode` ("LPDDR4 only" when the row carries note [11] or the part is LPDDR4) and `ranks = 2` when the density cell says "2cs". Entries without a second part (SK hynix, Nanya) match every suffix, as the list's first-part rule says (Ver 2.65 p. 3).

## Channels

| Channel | Read through | Key |
|---|---|---|
| Digi-Key | public search and product pages (the JSON they embed); full price table from the product page | none |
| LCSC | JLCPCB parts API (`selectSmtComponentList`), which carries LCSC stock and prices | none |
| Mouser | Mouser Search API v1 | `MOUSER_API_KEY` |
| Newark | element14 Product Search API, store `www.newark.com` | `ELEMENT14_API_KEY` |
| Arrow | Arrow itemservice v4 | `ARROW_API_KEY` and `ARROW_LOGIN` |
| TTI | TTI Search API v1 | `TTI_API_KEY` |
| Mouser, Newark, Arrow, TTI, Future, TME, RS, brokers | FindChips search page, when the channel's key is not set | none |

A channel whose key is not set is read through FindChips; if FindChips fails too, the channel is reported as skipped, never as zero stock. FindChips shows only the distributors that list the part with it, so "no row" there means "not listed on FindChips", which the coverage table states per query. The keyed adapters parse the response shapes in the vendors' API documentation; their tests run on fixtures written from that documentation.

Every fetch is cached under `~/.cache/opendap/sourcing` with the time it was read (`--cache`, `--max-age`, default 24 h); `--offline` rebuilds a matrix from the cache alone. Requests to one host are at least one second apart.

**Authorized.** A listing counts as authorized when it comes from Digi-Key's own catalogue (not its marketplace), from a keyed distributor API, or from a FindChips block marked "Authorized Distributor" or belonging to a franchised distributor (Mouser, Newark, Arrow, TTI, Future, TME, RS, Farnell, Avnet). FindChips' marker is per distributor, not per product line. Other FindChips sellers count as brokers: their stock is shown, never counted as authorized. LCSC is a channel of its own: its authorization for a given maker is not stated by the data, so its stock counts for prototypes and the DIY path, not as authorized stock.

## Verdict and score

Each candidate gets reasons, each `pass`, `conditional` or `fail`; the verdict is the worst of them.

1. **Gates** (compatibility, from the candidates file). A failed gate fails the candidate and ranks it after every candidate that passes its gates (T1-028: "among compatible parts").
2. **Availability class**, from the listings:
   - **A**: authorized stock ≥ build quantity (pass);
   - **B**: authorized stock > 0 but below it (conditional);
   - **D**: listed at an authorized channel with no stock, a factory order (conditional);
   - **C**: stock at LCSC only (conditional);
   - **E**: no authorized listing; broker stock is reported (fail).
   D ranks above C: an authorized listing, even without stock, scores above stock outside authorized channels.
3. **Lifecycle**: the worst statement among the candidate file's and the channels' (active > not stated > not recommended / last time buy > obsolete). Obsolete fails; not stated, NRND and last time buy are conditional. "Discontinued at Digi-Key" is the distributor dropping the line and counts as NRND.
4. **Price**: the build price is the unit price for the build quantity, from the cheapest authorized offer whose stock covers the build, else any stocked authorized offer, else an authorized list price with no stock, else LCSC with stock. When the minimum order is above the build quantity, the MOQ and its price are used and shown.

**Score** = availability (A 4, B 3, D 2, C 1, E 0) × 100 + lifecycle (active 3, not stated 2, NRND or LTB 1, obsolete 0) × 10 + price points (0–9: 9 × the cheapest build price among the gate-passing candidates of the same availability and lifecycle class, over this one's). The order is therefore availability first, then lifecycle, then price.

## Outputs

In `--out DIR` (required; a private location):

- `matrix.md`: the ranked table, the reasons per candidate and the channel coverage per query; URLs are autolinks (`<https://…>`), so the file passes markdownlint without exclusions;
- `matrix.csv`: one row per candidate (prices with stock, list prices, LCSC prices, build price and its basis, first and last fetch time, reasons);
- `listings.csv`: one row per listing (channel, seller, authorized, MPN as listed, stock, every price break, lifecycle wording, SKU, URL, source, fetch time);
- `matrix.json`: everything, schema `opendap.sourcing-matrix/v1`: the title, the generation time, the build and price quantities, `columns` (the candidates file's column list, in its order) and every result with its listings.

## Re-rendering a saved matrix

`odapkit parts matrix --render DIR/matrix.json --out DIR2` rebuilds `matrix.md`, `matrix.csv` and `listings.csv` from a saved `matrix.json`, without fetching anything: the results, their order and the generation time are the saved ones, so only a change in the renderer changes the files. The JSON is read, never rewritten.

The attribute columns come from the JSON's `columns`. A `matrix.json` written before it recorded them needs `--columns a,b,c`, in the candidates file's order (the attribute columns of its `matrix.md` header); `--columns` also replaces a recorded list. A column that no candidate has as an attribute, a JSON without columns and without `--columns`, or a file that is not an `opendap.sourcing-matrix/v1` document is a usage or file error (exit 2). `--json` prints the document used, with its columns; otherwise the Markdown matrix is printed.

## Limits

- Stock and prices change daily; a matrix is evidence for its fetch date only.
- Page layouts change: a page without the expected data is reported as an error for that channel and query, not as zero stock.
- Lifecycle from distributors is their wording; a maker's statement goes in the candidates file with its source.
- Volume quotes from makers and distributors are outside the tool.
