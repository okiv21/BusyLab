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
        # Ecommerce exports timestamp events as "<verb>ed at".
        (STRONG, ("paid at", "fulfilled at", "shipped at", "cancelled at",
                  "closed at", "updated at", "ordered at", "processed at",
                  "doc date", "value date", "week ending", "month ending",
                  "invoice dt", "sale dt", "order dt", "datum")),
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
        (STRONG, ("model", "model name", "part no", "part number", "variant",
                  "variant title", "lineitem name", "line item", "style",
                  "style code", "barcode", "upc", "ean", "isbn")),
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
        (STRONG, ("extended price", "extended amount", "line amount",
                  "net amount", "value sold", "amount sold", "sales total")),
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
        (ABBREV, ("qty", "qnty", "qnt", "nos", "quantiy", "qauntity", "quatity")),
        # "no of items" is a count; a bare "no" is an identifier suffix.
        (STRONG, ("no of", "number of", "no of items", "number of items")),
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
        (ABBREV, ("ppu", "u price", "prc", "pric", "unitprice", "mrp")),
        (STRONG, ("lineitem price", "item price", "price per pc")),
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
        (STRONG, ("billing name", "billing customer", "shipping name",
                  "member no", "member number", "account no")),
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
        (STRONG, ("docket no", "docket number", "waybill", "waybill no",
                  "slip no", "voucher no", "trans no", "tran id")),
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
        (WEAK, ("source", "method", "how")),
        # Only channel-shaped when something qualifies them.
        (STRONG, ("order type", "delivery type", "sale type",
                  "order mode", "delivery mode")),
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
        (STRONG, ("postcode", "post code", "zip", "zip code", "postal code",
                  "lga", "local government", "shipping city", "billing city",
                  "shipping state", "county", "district")),
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
        (STRONG, ("sub category", "subcategory", "sub cat", "main category",
                  "product line", "line", "collection")),
        (WEAK, ("segment", "kind", "sort", "type", "grouping", "tag")),
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
        (STRONG, ("payment", "tender", "paid via", "paid with", "pay mode",
                  "paid by", "paid using", "payment mode", "pay method")),
        (ABBREV, ("pay type", "pmt method", "pay mthd", "paymnt")),
        (WEAK, ("pay", "settlement")),
    ),
}


# Match quality multipliers, applied to a term's weight.
_EXACT = 1.0  # the whole column name is exactly this term
_PHRASE_TAIL = 0.95  # consecutive words, and they end the name
_PHRASE = 0.85  # the term appears as consecutive words in the name
_BAG = 0.7  # all the term's words are present, but scattered
_PHRASE_HEAD = 0.7  # consecutive words, but only at the start of a longer name
_INFIX = 0.5  # a single-word term is buried inside a longer word

# Why the head and tail multipliers differ, since it looks like a fudge:
#
# English compound nouns are head-final. "Invoice date" is a kind of date, not
# a kind of invoice; "sale dt", "paid at" and "order number" work the same way.
# So the last word of a column name is far better evidence of what the column
# holds than the first, and without that asymmetry the qualifier wins on raw
# weight - "invoice" outscored "dt" and a perfectly ordinary date column was
# read as an order id.
#
# It also protects the case the spec cares about most (3.2): "discount_amount"
# keeps its DISCOUNT reading, because "discount" leads a two-word name and the
# generic "amount" that follows is deliberately weak.


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
    return _run_position(haystack, needle) is not None


def _run_position(haystack: list[str], needle: list[str]) -> int | None:
    """Index of the first consecutive run of ``needle``, or None.

    The position matters, not just the presence: a term ending the name is
    describing what the column is, and a term starting it is usually only
    qualifying whatever comes after.
    """
    n = len(needle)
    if n == 0 or n > len(haystack):
        return None
    for i in range(len(haystack) - n + 1):
        if haystack[i : i + n] == needle:
            return i
    return None


def _score_term(name: str, tokens: list[str], term: str) -> tuple[float, str] | None:
    """Return (multiplier, quality) for a term against a normalised name."""
    term_tokens = term.split()
    if not term_tokens:
        return None
    if name == term:
        return _EXACT, "exact"

    position = _run_position(tokens, term_tokens)
    if position is not None:
        end = position + len(term_tokens)
        # "of" inverts the compound: in "date of order" the head is "date",
        # not "order". So the head region ends at "of" when one is present,
        # rather than at the end of the name.
        head_end = tokens.index("of") if "of" in tokens else len(tokens)
        if end == head_end:
            return _PHRASE_TAIL, "phrase_tail"
        if position == 0:
            return _PHRASE_HEAD, "phrase_head"
        return _PHRASE, "phrase"

    if len(term_tokens) > 1 and all(t in tokens for t in term_tokens):
        return _BAG, "bag"
    if len(term_tokens) == 1 and len(term) >= 3:
        # "qtysold" should still hit "qty". Require length 3 to stop "no"
        # and "id" matching half the alphabet.
        if any(term in tok and tok != term for tok in tokens):
            return _INFIX, "infix"
    return None


#: Money columns that are none of the thirteen roles.
#:
#: Tax, shipping and service charges are all amounts sitting next to a sale, so
#: the deliberately-weak generic terms ("amount", "value", "total") pick them up
#: and the greedy assignment then has to put them somewhere. In practice that
#: meant ``tax_amount`` being proposed as a discount and ``shipping_fee`` as a
#: cost - both wrong, both plausible enough to be waved through, and both
#: corrupting margin if they were.
#:
#: Proposing nothing is the right answer. These are not roles the engine has,
#: so the column becomes unknown and is left out, rather than becoming a
#: confirmation prompt offering only wrong options.
NON_MEASURE_HEADS: frozenset[str] = frozenset(
    {
        "tax", "vat", "gst", "duty", "levy", "withholding", "wht",
        "shipping", "freight", "delivery", "postage", "carriage", "handling",
        "service", "surcharge", "gratuity", "tip", "tips",
        "commission", "fee", "fees", "charge", "charges",
        "refund", "refunds", "credit", "chargeback", "adjustment",
        "balance", "outstanding", "deposit", "change", "rounding",
    }
)


#: The roles a non-measure head suppresses. Only the money ones: those are the
#: readings that content cannot distinguish and that corrupt arithmetic when
#: wrong. A tax column is not a cost, but "Shipping City" is still a region.
MONETARY_ROLES: frozenset[Role] = frozenset(
    {Role.REVENUE, Role.COST, Role.DISCOUNT, Role.UNIT_PRICE}
)


def _is_non_measure(tokens: list[str]) -> bool:
    """True when the name's head word puts it outside the role vocabulary.

    Head word only. "tax amount" is a tax; "amount of tax" is too. But
    "total sales tax exclusive revenue" is contrived, and a name whose *first*
    word is one of these is reliably not a sales measure.
    """
    if not tokens:
        return False
    if tokens[0] in NON_MEASURE_HEADS:
        return True
    # "amount of tax", "cost of shipping" - the "of" inversion again.
    if "of" in tokens:
        after = tokens[tokens.index("of") + 1 :]
        if after and after[0] in NON_MEASURE_HEADS:
            return True
    return False


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

    if _is_non_measure(tokens):
        # Suppress only the monetary readings, not every role. "Shipping City"
        # is a location that happens to start with a non-measure word, and
        # dropping the whole name would lose a perfectly good region column.
        for role in MONETARY_ROLES:
            best.pop(role, None)

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
