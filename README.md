# BusyLab

A business analyst in a box. Point it at a sales spreadsheet and it surfaces
the non-obvious truths hidden inside it.

This repository is the **analysis engine**: a pure Python library that runs
standalone against a file on disk, with no server, no web stack and no
deployment involved. That is spec 9's one non-negotiable, and everything here
is built to keep it true. The API, when it exists, will be a thin wrapper
around this package and never the other way round.

## Status

| Build step (spec 10) | State |
|---|---|
| 1. Detection engine | **done** |
| 2. Core analysis engine | **done** (Pillar 0) |
| 3. Narration and routing | next |
| 4. Story-led UI | not started |
| 5. Background jobs | not started |
| 6. Mapping memory, Sheets, folder watch, quality gate | fingerprint done, rest not started |
| 7-11 | not started |

## Try it

```bash
python -m venv .venv
./.venv/Scripts/python.exe -m pip install -e ".[dev]"

# Run the engine against a file and see what it understood
./.venv/Scripts/python.exe -m busylab samples/messy_sales.xlsx
./.venv/Scripts/python.exe -m busylab samples/monthly_tabs.xlsx

./.venv/Scripts/python.exe -m pytest
```

## How detection works

Three layers, in the order spec 3.2 requires: **keywords propose, content
verifies, the user confirms only the ambiguous.**

**Layer 1 — `detection/keywords.py`.** A per-role dictionary including
abbreviations, real misspellings and Nigerian context terms (naira, LGA, amt,
qty, desc). Every term carries a weight. Words that mean exactly one thing
(`revenue`, `cogs`, `customer id`) score high; words that appear in half the
money columns ever written (`amount`, `total`, `value`) score deliberately
low. That weighting is what stops `discount_amount` being read as revenue,
which spec 3.2 names as the dangerous silent failure.

**Layer 2 — `detection/content.py`.** Profiles the values and scores how
plausible each role is. It does two things keywords structurally cannot:
*rescue* a column called `Column3` that parses cleanly as dates, and *veto* a
column called `date` that holds `"Lagos, Ikeja"`.

**Layer 3 — `detection/engine.py`.** Blends the two, assigns roles greedily so
the strongest evidence claims its role first, and raises a question **only**
where the layers disagree or two columns genuinely compete. A clean file
produces zero prompts. A messy file is asked about its own mess and nothing
else.

### Decisions worth knowing

- **Content cannot name a label column.** Channel, region, category and
  payment method are all just "a few repeated text values". Content can see
  the shape but not the meaning, so these roles require keyword support. An
  unrecognised label column is offered as a generic grouping instead, which is
  the middle path of spec 3.3 rather than a coin toss.
- **Cross-column checks are row-wise.** `median(qty) x median(price)` is not
  `median(revenue)`. Comparing column medians reports a perfectly consistent
  file as inconsistent the moment quantity varies, which is always.
- **Cost basis is settled by stability, not size.** Whether a cost column is
  per-unit or a line total inverts every margin. If cost is per-unit then
  `cost x qty / revenue` is near-constant while `cost / revenue` swings with
  order size; if it is a line total the reverse holds. The steadier ratio wins.
- **Revenue earns confidence arithmetically.** A column called `Amount` is
  weak on its name alone, but if it equals `qty x price` row by row and is the
  largest money column in the file, that is real evidence and the user is not
  interrupted.

## How analysis works

Every analysis takes a `SalesFrame` and returns `Finding` objects. A finding is
structured, numeric and evidenced — never a sentence and never advice. The
chart is a deterministic function of the finding's type (spec 7), so the shape
of the insight picks the visual and nothing downstream guesses.

Implemented (Pillar 0, the non-obvious layer):

| Analysis | What it answers |
|---|---|
| `revenue_trend` | Is this a real move or normal variation? Deseasonalised first. |
| `seasonality` | Is there a repeating annual shape, and how strong? |
| `margin_reality` | Is the best seller actually the best earner? |
| `concentration_risk` | How much of the business rests on how few products? |
| `revenue_decomposition` | Which products caused the change? |
| `dimension_decomposition` | "Dying online, fine in store" |
| `price_volume_split` | Fewer units, or lower prices? |
| `segmentation` | Do groups genuinely differ, after FDR correction? |
| `product_relationships` | What moves together, with the causation caveat attached |
| `product_ranking` | Table stakes. Ranked last on purpose. |

### Decisions worth knowing

- **Multiple comparisons are corrected, always.** Benjamini-Hochberg across
  each whole family of tests. Bonferroni was rejected as too conservative on a
  family of hundreds: it would suppress the real findings along with the false
  ones. On 200 tests of pure noise, naive testing reports about 10 findings and
  this reports none — which is the difference between a product and a random
  number generator with opinions.
- **Statistical and material significance are different.** A large enough
  sample makes a 2% drift detectable. It is still not news, so a movement must
  clear both bars before it is called a trend.
- **Change is measured from the fitted trend, not from the first and last
  data points.** Two individual periods are noisy and comparing them is exactly
  the eyeballing this product exists to replace.
- **Nothing is invented on quiet data.** `fixtures.flat_business` is a control
  with no trend, no concentration and no real group differences; the engine
  must return no significant findings for it, and a test enforces that.
- **No finding may read as advice.** Spec 2 is the hardest rule in the product,
  so `check_non_directive` enforces it mechanically over every summary and the
  test suite fails on a violation. Directive AI carries liability; illuminating
  AI is trusted.

## The mess it absorbs

`loading.py` repairs structure before detection sees a frame, because real SME
workbooks are not rectangular (spec 3.1):

- title banners and logos above the header
- merged, two-row headers
- `TOTAL` / `Subtotal` / `GRAND TOTAL` rows sitting inside the data
- blank spacer rows and columns
- one tab per month, combined into a single table with a `_sheet` column

`cleaning.py` repairs values: `₦1,234.50`, `(500)` for negatives, `12%`,
`n/a`, Excel date serials, and `15/03/2026` vs `03/15/2026` resolved by
whichever ordering parses more of the column.

Fixtures in `tests/fixtures.py` reproduce every one of these deliberately, so
the engine is tested against the mess it exists to absorb rather than against
tidy data it will never see.

## Layout

```
busylab/
  roles.py            role vocabulary, tiers, what each role unlocks
  findings.py         Finding contract, chart mapping, non-directive guard
  cleaning.py         value coercion (currency, percents, dates)
  loading.py          structural repair, sheet combining
  detection/
    keywords.py       Layer 1
    content.py        Layer 2
    engine.py         Layer 3, assignment, prompts, fingerprint
  analysis/
    dataset.py        canonical SalesFrame, profit and revenue derivation
    stats.py          significance, seasonality, FDR correction
    core.py           Pillar 0 analyses
    segments.py       segmentation and correlation, both FDR-corrected
    engine.py         orchestration and story ranking
  cli.py              python -m busylab <file>
tests/
  fixtures.py         messy workbooks, planted truths, flat control
  test_detection.py
  test_analysis.py
```
