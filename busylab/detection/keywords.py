"""Layer 1: the keyword dictionary.

Keywords *propose*, they never decide (spec 3.2). This layer looks only at the
column's name and returns a ranked list of candidate roles with a score. It is
fast, it catches most real files, and it is wrong often enough that Layer 2
must always get a say.

The weighting is the whole trick. A word that only ever means one thing
("revenue", "cogs", "customer id") scores high. A word that shows up in half
the money columns ever written ("amount", "total", "value") scores low, so a
name like ``discount_amount`` is won by the specific term "discount" rather
than the generic term "amount". Spec 3.2 calls that silent mislabelling the
dangerous failure case, and low generic weights are the defence.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..roles import Role

# Weight bands. Kept as named constants so the tables below read as intent
# rather than as a wall of magic numbers.
DEFINING = 1.0  # the word means this role and essentially nothing else
STRONG = 0.8  # strongly implies the role, minor ambiguity
ABBREV = 0.65  # a common short form, slightly riskier
WEAK = 0.4  # generic; needs Layer 2 to confirm anything

#: role -> ((weight, terms), ...). Terms are matched after normalisation, so
#: write them in plain lowercase words; "order_date" and "orderDate" both
#: normalise to "order date".
_TERMS: dict[Role, tuple[tuple[float, tuple[str, ...]], ...]] = {
    Role.DATE: (
        (
            DEFINING,
            (
                "date",
                "order date",
                "sale date",
                "sales date",
                "transaction date",
                "txn date",
                "invoice date",
                "purchase date",
                "date of sale",
                "date sold",
                "trade date",
                "posting date",
                "created at",
                "date created",
                "timestamp",
            ),
        ),
        (
            STRONG,
            ("day", "period", "month", "week", "datetime", "time stamp", "sold on"),
        ),
        (ABBREV, ("dt", "dte", "trans date", "ord date", "date time")),
        # Misspellings seen in the wild.
        (ABBREV, ("dat", "datte", "orderdate", "salesdate")),
        (WEAK, ("time", "when", "entry date", "record date")),
    ),
    Role.PRODUCT: (
        (
            DEFINING,
            (
                "product",
                "product name",
                "item name",
                "item description",
                "product description",
                "sku",
                "product code",
                "item code",
                "stock code",
                "article",
                "product title",
            ),
        ),
        (
            STRONG,
            (
                "item",
                "goods",
                "merchandise",
                "commodity",
                "particulars",
                "description of goods",
                "stock item",
                "product id",
            ),
        ),
        (ABBREV, ("prod", "itm", "desc", "prod name", "prod desc", "item desc")),
        (ABBREV, ("produkt", "prduct", "itemname")),
        (WEAK, ("name", "title", "description", "details", "what")),
    ),
    Role.REVENUE: (
        (
            DEFINING,
            (
                "revenue",
                "total revenue",
                "sales revenue",
                "gross revenue",
                "net revenue",
                "turnover",
                "total paid",
                "amount paid",
                "sales amount",
                "sales value",
                "total sales",
                "line total",
                "gross sales",
                "net sales",
                "total amount",
            ),
        ),
        (
            STRONG,
            (
                "sales",
                "sale",
                "paid",
                "gross",
                "income",
                "takings",
                "receipts",
                "grand total",
                "sub total",
                "order value",
                "order total",
                "invoice total",
                "amount naira",
                "naira value",
            ),
        ),
        (ABBREV, ("rev", "amt paid", "ttl", "tot amt", "sls", "revnue", "revenu")),
        # Deliberately weak: these words appear in cost, discount and tax
        # columns just as often as they appear in revenue columns.
        (WEAK, ("amount", "amt", "total", "value", "naira", "ngn", "money", "sum")),
    ),
    Role.QUANTITY: (
        (
            DEFINING,
            (
                "quantity",
                "quantity sold",
                "qty sold",
                "units sold",
                "no of units",
                "number sold",
                "number of items",
                "item count",
            ),
        ),
        (STRONG, ("units", "pieces", "pcs", "count", "volume", "unit sold")),
        (ABBREV, ("qty", "qnty", "qnt", "nos", "no", "quantiy", "qauntity", "quatity")),
        (WEAK, ("unit", "number", "num", "each")),
    ),
    Role.UNIT_PRICE: (
        (
            DEFINING,
            (
                "unit price",
                "price per unit",
                "selling price",
                "sale price",
                "price each",
                "unit rate",
                "list price",
                "retail price",
                "price per item",
            ),
        ),
        (STRONG, ("price", "rate", "sp", "unit selling price")),
        (ABBREV, ("ppu", "u price", "prc", "pric", "unitprice")),
        (WEAK, ("per unit", "each price")),
    ),
    Role.COST: (
        (
            DEFINING,
            (
                "cost",
                "unit cost",
                "cost price",
                "cost per unit",
                "cogs",
                "cost of goods",
                "cost of goods sold",
                "buying price",
                "purchase price",
                "landed cost",
                "product cost",
                "total cost",
            ),
        ),
        (STRONG, ("cp", "wholesale price", "supplier price", "cost value", "expense")),
        (ABBREV, ("cst", "unit cst", "cost pr", "buy price", "cos")),
        (WEAK, ("spend", "outlay")),
    ),
    Role.CUSTOMER_ID: (
        (
            DEFINING,
            (
                "customer id",
                "customer code",
                "customer name",
                "client id",
                "client name",
                "buyer id",
                "cust id",
                "customer number",
                "member id",
                "account id",
                "customer",
                "client",
            ),
        ),
        (STRONG, ("buyer", "account", "member", "patron", "customer ref")),
        (ABBREV, ("cust", "custid", "cust name", "cid", "custmer", "custome")),
        # Nigerian SMEs very often identify a repeat customer by phone number.
        (WEAK, ("phone", "phone number", "mobile", "contact", "email")),
    ),
    Role.ORDER_ID: (
        (
            DEFINING,
            (
                "order id",
                "order no",
                "order number",
                "invoice no",
                "invoice number",
                "receipt no",
                "receipt number",
                "transaction id",
                "txn id",
                "sale id",
                "basket id",
                "cart id",
                "bill no",
            ),
        ),
        (STRONG, ("order", "invoice", "receipt", "transaction", "bill", "waybill")),
        (ABBREV, ("ord no", "inv no", "inv", "txn", "trx", "ref no", "order ref")),
        (WEAK, ("ref", "reference", "id", "no", "number")),
    ),
    Role.CHANNEL: (
        (
            DEFINING,
            (
                "channel",
                "sales channel",
                "order channel",
                "sale channel",
                "order source",
                "sales medium",
                "mode of sale",
                "selling channel",
            ),
        ),
        (STRONG, ("platform", "medium", "outlet type", "sale type", "order type")),
        (ABBREV, ("src", "chan", "chnl", "via", "channe")),
        (WEAK, ("source", "type", "mode", "method", "how")),
    ),
    Role.REGION: (
        (
            DEFINING,
            (
                "region",
                "state",
                "city",
                "location",
                "territory",
                "zone",
                "geo",
                "lga",
                "local government",
                "country",
                "province",
            ),
        ),
        (STRONG, ("branch", "store", "outlet", "shop", "site", "area", "town")),
        (ABBREV, ("loc", "regn", "citi", "brnch")),
        (WEAK, ("address", "place", "where", "market")),
    ),
    Role.DISCOUNT: (
        (
            DEFINING,
            (
                "discount",
                "discount amount",
                "discount value",
                "discount given",
                "discount pct",
                "discount percent",
                "rebate",
                "markdown",
                "price reduction",
            ),
        ),
        (STRONG, ("promo", "promotion", "offer", "reduction", "waiver", "coupon")),
        (ABBREV, ("disc", "disc amt", "dsc", "discnt", "promo amt")),
        (WEAK, ("less", "off", "deduction", "allowance")),
    ),
    Role.CATEGORY: (
        (
            DEFINING,
            (
                "category",
                "product category",
                "item category",
                "product type",
                "product group",
                "product class",
                "department",
                "product family",
            ),
        ),
        (STRONG, ("cat", "group", "class", "family", "dept", "brand", "range")),
        (ABBREV, ("prod cat", "item cat", "categ", "catgory")),
        (WEAK, ("segment", "kind", "sort")),
    ),
    Role.PAYMENT_METHOD: (
        (
            DEFINING,
            (
                "payment method",
                "payment type",
                "pay method",
                "mode of payment",
                "payment mode",
                "method of payment",
                "payment channel",
            ),
        ),
        (STRONG, ("payment", "tender", "paid via", "paid with", "pay mode")),
        (ABBREV, ("pay type", "pmt method", "pay mthd", "paymnt")),
        (WEAK, ("pay", "settlement")),
    ),
}


# Match quality multipliers, applied to a term's weight.
_EXACT = 1.0  # the whole column name is exactly this term
_PHRASE = 0.85  # the term appears as consecutive words in the name
_BAG = 0.7  # all the term's words are present, but scattered
_INFIX = 0.5  # a single-word term is buried inside a longer word


@dataclass(frozen=True)
class KeywordMatch:
    """One role proposed for a column, with the evidence behind it."""

    role: Role
    score: float
    term: str
    quality: str

    @property
    def exact(self) -> bool:
        return self.quality == "exact"


_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_NON_ALNUM = re.compile(r"[^a-z0-9]+")
_CURRENCY = re.compile(r"[₦$£€¥]")


def normalize(name: object) -> str:
    """Reduce a raw column name to lowercase words separated by single spaces.

    ``"Order_Date"``, ``"orderDate"`` and ``"ORDER DATE "`` all become
    ``"order date"``. Currency symbols become the word "naira" only when they
    are the naira sign, otherwise they are dropped; a column called ``"Total (₦)"``
    should still read as money.
    """
    text = "" if name is None else str(name)
    text = text.replace("₦", " naira ")
    text = _CURRENCY.sub(" ", text)
    text = _CAMEL.sub(" ", text)
    text = text.lower()
    text = _NON_ALNUM.sub(" ", text)
    return " ".join(text.split())


def _contiguous(haystack: list[str], needle: list[str]) -> bool:
    """True if ``needle`` appears as a consecutive run inside ``haystack``."""
    n = len(needle)
    if n == 0 or n > len(haystack):
        return False
    return any(haystack[i : i + n] == needle for i in range(len(haystack) - n + 1))


def _score_term(name: str, tokens: list[str], term: str) -> tuple[float, str] | None:
    """Return (multiplier, quality) for a term against a normalised name."""
    term_tokens = term.split()
    if not term_tokens:
        return None
    if name == term:
        return _EXACT, "exact"
    if _contiguous(tokens, term_tokens):
        return _PHRASE, "phrase"
    if len(term_tokens) > 1 and all(t in tokens for t in term_tokens):
        return _BAG, "bag"
    if len(term_tokens) == 1 and len(term) >= 3:
        # "qtysold" should still hit "qty". Require length 3 to stop "no"
        # and "id" matching half the alphabet.
        if any(term in tok and tok != term for tok in tokens):
            return _INFIX, "infix"
    return None


def match_name(name: object) -> list[KeywordMatch]:
    """Propose roles for a single column name, best first.

    Returns every role that matched at all. Callers should treat the scores as
    a proposal to be checked against the column's actual contents, never as an
    answer on their own.
    """
    normalized = normalize(name)
    if not normalized:
        return []
    tokens = normalized.split()

    best: dict[Role, KeywordMatch] = {}
    for role, bands in _TERMS.items():
        for weight, terms in bands:
            for term in terms:
                scored = _score_term(normalized, tokens, term)
                if scored is None:
                    continue
                multiplier, quality = scored
                score = round(weight * multiplier, 4)
                current = best.get(role)
                if current is None or score > current.score:
                    best[role] = KeywordMatch(
                        role=role, score=score, term=term, quality=quality
                    )

    return sorted(best.values(), key=lambda m: (-m.score, m.role.value))


def best_match(name: object) -> KeywordMatch | None:
    """The single strongest role proposal for a column name, if any."""
    matches = match_name(name)
    return matches[0] if matches else None


def all_terms() -> dict[Role, set[str]]:
    """Flatten the dictionary, for tests and for documentation."""
    return {
        role: {term for _, terms in bands for term in terms}
        for role, bands in _TERMS.items()
    }
