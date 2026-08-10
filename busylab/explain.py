"""What a finding actually means, for someone who does not do this for a living.

Every summary the engine writes is true, short and non-directive, and none of
that makes it *understandable*. "That movement is larger than this business's
normal variation" is a precise statement that assumes the reader already knows
why normal variation is the thing being compared against. "Online fell by
553.5k per period, which is 100% of the total move" assumes they know what a
period is and what a total move is. A reader who does not have that background
gets a wall of correct sentences and no idea what any of them are telling them.

So this module adds the missing half: what the finding *means*, in the words
someone would use out loud.

Two rules shape it.

**No numbers.** The summary already carries every number, verified against the
facts. Restating them here would double the reader's work and reintroduce the
invented-number risk for no gain. These sentences explain the *kind* of thing
that has been found, which is exactly what a confused reader is missing, and
being number-free means they cannot be wrong about the arithmetic.

**Still not directive.** Spec 2 holds here as firmly as anywhere else. It is a
short step from "what this means" to "what you should do", and this is the most
tempting place in the codebase to take it. Explaining that one product carries
most of the profit is insight; suggesting the owner diversify is advice. Every
string below is checked against the same non-directive guard the engine and the
narration layer use, by a test that reads them all.
"""

from __future__ import annotations

from .findings import Finding, FindingType

#: Meaning by finding type. The fallback when nothing more specific applies.
#:
#: Written as "here is what this kind of finding tells you", because the reader
#: has just read a sentence full of numbers and needs to know what sort of
#: statement it was.
_BY_TYPE: dict[FindingType, str] = {
    FindingType.TREND: (
        "This is a real change in direction, not an ordinary good or bad "
        "stretch. Every business bounces around from month to month; this "
        "movement is bigger than the bouncing, which is what makes it worth "
        "separating from the noise."
    ),
    FindingType.NOISE: (
        "This looks like a change but is within the range this business "
        "normally moves in anyway. It is here so you know it was checked and "
        "came back as ordinary variation rather than something new."
    ),
    FindingType.RANKING: (
        "This is the order, largest to smallest. Useful mainly for what it "
        "puts near the top that you might have assumed was further down."
    ),
    FindingType.CONCENTRATION: (
        "This is about how much rests on one thing. When a large share of the "
        "total sits in a single product or customer, the whole business "
        "follows whatever happens to it - in both directions."
    ),
    FindingType.TENSION: (
        "Two measures disagree here. Something can bring in a lot of money "
        "and still keep very little of it once its costs come out, so the "
        "biggest seller and the biggest earner are often not the same thing."
    ),
    FindingType.DECOMPOSITION: (
        "This is where a change came from. The overall number moved, and this "
        "breaks that movement into the parts responsible, so a total that "
        "looks like a broad decline often turns out to be one or two specific "
        "things moving."
    ),
    FindingType.SEASONALITY: (
        "This is a shape that repeats at the same time each year. It matters "
        "because a rise or fall that happens every year at this point is not "
        "news, and comparing against last month would call it news."
    ),
    FindingType.SEGMENTATION: (
        "The groups here behave differently from each other by more than "
        "chance would produce. The difference is in the data rather than in "
        "how the data happened to be split."
    ),
    FindingType.REPEAT_VS_NEW: (
        "This separates people coming back from people arriving for the first "
        "time. The two move for completely different reasons, and a total "
        "that holds steady can hide one of them falling while the other rises."
    ),
    FindingType.FORECAST: (
        "This is where the recent pattern points, with a range around it. The "
        "range is the honest part: it shows how much the figure could "
        "reasonably differ, and a wide range means the pattern so far does "
        "not pin the future down tightly."
    ),
    FindingType.CUSTOMER_SEGMENTS: (
        "Customers are grouped by how recently they bought, how often, and "
        "how much. Grouping them this way separates the ones who are simply "
        "quiet from the ones who appear to have stopped."
    ),
    FindingType.COHORT_RETENTION: (
        "This follows each month's new customers forward to see how many are "
        "still buying later. It answers whether customers are staying longer "
        "than they used to, which a total customer count cannot show."
    ),
    FindingType.RELATIONSHIP: (
        "These items rise and fall together across periods. Moving together "
        "is not the same as one causing the other - a shared season or a "
        "shared promotion will do it too."
    ),
    FindingType.BASKET: (
        "These items turn up in the same order more often than their "
        "individual popularity would explain. That is a pattern in how people "
        "actually shop, not just in what sells well."
    ),
    FindingType.GOAL_PACE: (
        "This compares where you are against where the target needs you to be "
        "by now, rather than against the target itself. Being behind early is "
        "a different situation from being behind at the end."
    ),
    FindingType.DATA_QUALITY: (
        "This is about the file rather than the business. It is here because "
        "it affects how much weight the other findings can carry."
    ),
}

#: Meaning for specific findings, where the type is too broad to be useful.
#:
#: Keyed on finding id. These win over the type-level text because a reader
#: hitting "Gift Box sells at a loss" needs to know what selling at a loss
#: means here, not what a tension finding is in general.
_BY_ID: dict[str, str] = {
    "loss_making_product": (
        "This product costs more to sell than it brings in, so each sale "
        "leaves the business with slightly less than it started with. Selling "
        "more of it makes that larger, not smaller."
    ),
    "margin_reality": (
        "The product with the biggest sales figure is not the one that keeps "
        "the most money. Revenue is what comes in; margin is what is left "
        "after the cost of the goods, and a busy low-margin line can be worth "
        "less than a quiet high-margin one."
    ),
    "concentration": (
        "A large share of the total comes from a single line. That is not "
        "automatically bad - it is often just what a focused business looks "
        "like - but it does mean the business and that one line move together."
    ),
    "revenue_trend": (
        "Revenue has moved in one direction across the whole period by more "
        "than this business's usual month-to-month movement. That is what "
        "separates a trend from a run of ordinary months."
    ),
    "revenue_decomposition": (
        "The overall revenue change is broken into the products behind it. A "
        "total that fell does not mean everything fell; usually a few lines "
        "moved and the rest stayed where they were."
    ),
    "decomposition_channel": (
        "The change is traced to where the sales happen rather than to what "
        "was sold. A drop concentrated in one channel is a different "
        "situation from the same drop spread evenly across all of them."
    ),
    "decomposition_region": (
        "The change is traced to location. One area moving while the others "
        "hold steady points somewhere quite different from every area sliding "
        "at once."
    ),
    "price_volume_split": (
        "A revenue change can only come from two places: how many units went "
        "out, or what each one sold for. Separating them says whether fewer "
        "people bought or whether the same people paid less."
    ),
    "rfm_segments": (
        "These are customers who used to order regularly and have since gone "
        "quiet for longer than their own habit would explain. They are "
        "counted separately from customers who were always occasional."
    ),
    "repeat_vs_new": (
        "Returning customers and first-time buyers are counted separately "
        "here. When one is rising and the other falling, the total hides it, "
        "and the two usually need thinking about differently."
    ),
    "product_relationships": (
        "These products move up and down together across periods. It can mean "
        "people buy them as a pair, or simply that the same thing affects both."
    ),
    "seasonality": (
        "There is a pattern that repeats at the same point each year. Knowing "
        "it exists is what stops an ordinary seasonal dip being read as a "
        "problem."
    ),
    "forecast": (
        "This continues the recent pattern forward and shows a range around "
        "it. The range widens the further out it goes, because the further "
        "ahead you look the less the past pins it down."
    ),
}

#: Plain definitions for the words the summaries cannot avoid using.
#:
#: The UI can show these on hover. They are here rather than in the frontend
#: because the wording is part of the product's honesty, not part of its
#: styling, and a definition that drifts from what the engine computed would be
#: worse than none.
GLOSSARY: dict[str, str] = {
    "revenue": "The money that came in, before any costs are taken off.",
    "profit": "What is left from revenue once the cost of the goods is taken off.",
    "margin": (
        "The share of a sale you keep after its costs - so a 30% margin means "
        "30p of every pound sold stays with the business."
    ),
    "cost": "What the goods themselves cost you, not counting rent or wages.",
    "units": "How many individual items were sold.",
    "average order value": "What a typical single order was worth.",
    "normal variation": (
        "How much this business moves up and down anyway, month to month, with "
        "nothing in particular happening. A change is only called real when it "
        "is bigger than this."
    ),
    "period": (
        "One step on the time axis of your data - usually a month, sometimes a "
        "week, depending on what the file contains."
    ),
    "trend": (
        "A movement in one direction that is larger than the normal "
        "month-to-month variation."
    ),
    "seasonality": "A pattern that repeats at the same time each year.",
    "channel": "Where a sale happened - online, in store, wholesale.",
    "segment": "A group of customers or products that behave alike.",
    "churn": "Customers who have stopped buying.",
    "retention": "The share of customers who come back and buy again.",
    "cohort": (
        "Everyone who first bought in the same month, followed forward "
        "together so their behaviour can be compared with later groups."
    ),
    "forecast": (
        "Where the recent pattern points if it continues, always shown with a "
        "range rather than a single number."
    ),
    "confidence range": (
        "The span the real figure is likely to fall inside. A wide span means "
        "the data does not pin it down closely."
    ),
    "concentration": "How much of a total sits in only one or two things.",
    "lift": (
        "How much more often two things are bought together than they would be "
        "by coincidence alone."
    ),
    "significant": (
        "Large enough that ordinary random variation is an unlikely "
        "explanation. It does not mean large enough to matter - that is a "
        "separate question, judged separately."
    ),
}


def explain(finding: Finding) -> str:
    """What this finding means, in plain words and without numbers.

    Specific text for the finding wins over generic text for its type, because
    a reader stuck on one sentence needs that sentence explained rather than a
    description of its category.
    """
    return _BY_ID.get(finding.id) or _BY_TYPE.get(finding.type, "")


def terms_in(text: str) -> list[str]:
    """Glossary terms appearing in ``text``, longest first.

    Longest first so that "average order value" is offered instead of "value"
    being matched inside it, and "normal variation" instead of nothing.
    """
    lowered = text.lower()
    found = [term for term in GLOSSARY if term in lowered]
    return sorted(found, key=len, reverse=True)


def glossary_for(finding: Finding) -> dict[str, str]:
    """The definitions worth offering alongside one finding.

    Drawn from the summary rather than from the explanation: the summary is
    where the unavoidable vocabulary lives, and the explanation is already
    written in words that need no gloss.
    """
    return {term: GLOSSARY[term] for term in terms_in(finding.summary)}
