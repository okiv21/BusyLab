# BusyLab

A business analyst in a box. Point it at a sales spreadsheet and it surfaces
the non-obvious truths hidden inside it: which product is quietly unprofitable,
whether a decline is real or just noise, where revenue is dangerously
concentrated. It presents all of it as a guided, visual story in plain
language.

Sector agnostic - telecom, restaurants, clothing brands, online stores, any
business with structured sales data, but selective about input: properly
structured tabular data only.

### What makes it different

**Insights, not directives.** BusyLab never says "remove this product" or
"raise the price". It surfaces true, evidenced findings and leaves the decision
entirely with the business owner. This is both respectful and strategic:
directive AI carries liability, illuminating AI is trusted. The rule is
enforced mechanically - a finding that reads as advice fails the test suite.

**Only the non-obvious.** "Product 6 sells the most" is useless; they packed
the boxes. The value is in what a human cannot see by eyeballing rows: margin
reality, statistical significance, seasonality-adjusted movement, concentration
risk, and the decomposition of why a number moved.

**The statistics are real; the model only narrates.** A deterministic engine
(pandas, statsmodels, scipy) does every computation. The language model's only
jobs are turning structured findings into English and routing questions to
pre-built analyses. It never computes, never decides, and never produces a
number - and it cannot, because both rules are enforced in code. BusyLab runs
fully without any model configured.

### Three parts

| | |
|---|---|
| `busylab/` | The engine. A pure Python library that runs standalone against a file on disk, with no server and no web stack. This is the one non-negotiable: the API is a thin wrapper around it, never the other way round. |
| `api/` | FastAPI. Accepts uploads, queues work, serves cached results. Imports the engine; the engine never imports it. |
| `web/` | Next.js. The story-led interface. |

## Status

| Build step (spec 10) | State |
|---|---|
| 1. Detection engine | **done** |
| 2. Core analysis engine | **done** (Pillar 0) |
| 3. Narration and routing | **done** |
| 4. Story-led UI | **done** |
| 5. Background jobs | **done** (API, worker, job table) |
| 6. Mapping memory, Sheets, folder watch, quality gate | **mapping memory + quality gate done**; Sheets and folder watch not started |
| 7. Forecasting | **done** (ARIMA, bands mandatory) |
| 8. Customer intelligence and goals | **done** (Pillars 3 and 4) |
| 9. Monitoring and alerts | **done** (anomalies, digest, scheduler) |
| 10. Present mode and exports | **done** |
| 11. Polish pass | **done** |
| Deployment | **configured**; needs your accounts |

## Getting started

Python 3.10+ and Node 18+.

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -e ".[dev]"      # Windows
# source .venv/bin/activate && pip install -e ".[dev]"   # macOS / Linux
```

**The engine on its own.** No server, no deployment, no API key - this is how
iteration actually happens:

```bash
python -m busylab path/to/sales.xlsx
python -m busylab path/to/sales.xlsx --ask "why did revenue drop?"
```

**The full app.** Two terminals:

```bash
python -m uvicorn api.main:app --reload --port 8000   # API + worker
cd web && npm install && npm run dev                  # http://localhost:3000
```

**Tests.**

```bash
python -m pytest
```

**Sample data.** The test fixtures generate deliberately messy workbooks and a
business with known, planted truths:

```bash
python -c "from tests import fixtures; \
  fixtures.planted_business().to_excel('sample.xlsx', index=False)"
```

### Optional: better prose

BusyLab is fully usable with no API key - the numbers and findings are
identical, only the wording is plainer. To enable narration, copy
`.env.example` to `.env` and add a free [Groq](https://console.groq.com/keys)
key:

```
GROQ_API_KEY=your_key_here
```

## How detection works

Three layers, in the order spec 3.2 requires: **keywords propose, content
verifies, the user confirms only the ambiguous.**

**Layer 1 - `detection/keywords.py`.** A per-role dictionary including
abbreviations, real misspellings and Nigerian context terms (naira, LGA, amt,
qty, desc). Every term carries a weight. Words that mean exactly one thing
(`revenue`, `cogs`, `customer id`) score high; words that appear in half the
money columns ever written (`amount`, `total`, `value`) score deliberately
low. That weighting is what stops `discount_amount` being read as revenue,
which spec 3.2 names as the dangerous silent failure.

**Layer 2 - `detection/content.py`.** Profiles the values and scores how
plausible each role is. It does two things keywords structurally cannot:
*rescue* a column called `Column3` that parses cleanly as dates, and *veto* a
column called `date` that holds `"Lagos, Ikeja"`.

**Layer 3 - `detection/engine.py`.** Blends the two, assigns roles greedily so
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
structured, numeric and evidenced - never a sentence and never advice. The
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
| `revenue_forecast` | Where total revenue is heading, with bands |
| `product_forecasts` | Per-product projection, and any heading below break-even |
| `repeat_vs_new` | Is it a discovery problem or a loyalty problem? |
| `rfm_segments` | Champions, At risk, Lost, New - sorted automatically |
| `cohort_retention` | Does each month's intake stick? |
| `basket_analysis` | What gets bought together, beyond chance |
| `goal_pace` | Will the target be hit, and where does the gap live |

### Decisions worth knowing

- **Multiple comparisons are corrected, always.** Benjamini-Hochberg across
  each whole family of tests. Bonferroni was rejected as too conservative on a
  family of hundreds: it would suppress the real findings along with the false
  ones. On 200 tests of pure noise, naive testing reports about 10 findings and
  this reports none - which is the difference between a product and a random
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

## Deploying

See **[DEPLOY.md](DEPLOY.md)** for the full walkthrough. Short version:
Supabase for Postgres and file storage, Render for the API and a separate
worker, Vercel for the frontend, GitHub Actions for the cron. All free tiers.

The one thing worth knowing before you start: **Render's disk is ephemeral.**
It spins down after inactivity and comes back empty, so SQLite and a local
uploads folder would silently lose every dataset overnight while appearing to
work. That is why the store and the file location are both abstractions -
SQLite and a folder locally, Postgres and Supabase Storage in production, with
the same interface either way.

A contract test runs the same assertions against both backends
(`tests/test_stores.py`), plus a static check that the two expose identical
method signatures, so they cannot drift. The Postgres half skips unless
`TEST_DATABASE_URL` is set - point it at your Supabase project once it exists
and the whole contract runs for real.

## The polish layer

Decoupled from the engine and done last, as spec 10 asks. Spec 7's rule is that
restraint reads as premium, so this is targeted rather than decorative.

- **Entrance animation runs once**, about half a second. Roughly 80% of the
  "cool" for very little cost.
- **Numbers count up**, eased out rather than linear - a linear counter reads
  like a loading spinner, decelerating reads like a value arriving and settling.
  The count drives the raw magnitude and formats every frame, so "-44%" counts
  through the percentages instead of scrambling characters. Tabular figures stop
  the width jittering as digits change.
- **One hero moment.** Exactly one card gets a soft accent glow; everything
  else stays calm so it lands.
- **Hover highlights the point and dims the rest.** Dimming the others rather
  than brightening the hovered one keeps the weight of the picture steady -
  raising one element makes a chart appear to flicker as the pointer travels.
  On the donut the centre figure follows the pointer, so hovering answers a
  question rather than just lighting something up.
- **`prefers-reduced-motion` is honoured in JavaScript, not only CSS.** Worth
  being explicit: the stylesheet zeroes CSS durations, but Framer Motion and
  the count-up write inline styles frame by frame, so a CSS rule cannot reach
  them. The media query is read directly and the animation is skipped.
- **Responsive padding.** Section padding is a custom property rather than a
  fixed value, because inline styles beat a stylesheet rule but happily read a
  variable - which is the one lever that shrinks a 60px gutter to 16px on a
  phone.

## Present mode and exports

**Present mode** plays the ranked story one finding at a time: chart animates
in, sentence with it, keyboard and click navigation, auto-advance, progress
dots. Almost free to build because every ingredient already existed - this is
those pieces played in sequence.

**Rendered MP4 is explicitly rejected** (spec Pillar 6). It is slow, expensive
per business and per refresh, and frozen the moment the numbers move. Present
mode covers the same need and updates the instant the data does.

**Voiceover is free.** The spec defers narration to a paid tier on the
assumption of a hosted TTS bill; the browser's own `speechSynthesis` has no
bill and no API key, so it is simply on for everyone. Availability is still
checked, because some Linux browsers ship with no voices installed.

**PDF and slide deck** are generated server-side from the same findings:

```
GET /datasets/{id}/export.pdf
GET /datasets/{id}/export.pptx
```

The deck carries **native, editable PowerPoint charts** rather than pictures,
so a recipient can restyle one for their own template or fix a label without
coming back. Where a finding's shape has no honest chart form - a cohort
triangle, a correlation matrix - it gets a clean table instead of a bad
picture. Both libraries are pure Python with nothing to compile, which matters
because install size is the deployment constraint.

A story the quality gate held cannot be exported. Putting a withheld analysis
into a board deck would launder exactly the thing the gate stopped.

## Monitoring and alerts

The retention layer (spec Pillar 2), and its failure mode is not missing an
event - it is **alert fatigue**. A system that cries wolf gets muted, and a
muted system protects nothing. Spec 11 lists sensitivity tuning as an open
question for exactly this reason, so the bias throughout is towards silence.
Four separate things hold it back:

**Robust statistics.** Anomalies are found with a median and a median absolute
deviation, not a mean and a standard deviation. The outlier being hunted sits
inside both the mean and the standard deviation it would be measured against,
so it inflates the bar and hides itself. On a series with two spikes, a plain
z-score reads 3.3 and misses them; the robust score reads 9.1.

**Seasonality first.** A December spike in a business that spikes every
December is not an anomaly.

**Multiple comparisons.** Twenty products checked weekly is twenty tests a
week, so about one false alarm a week by construction. Product anomalies are
FDR-corrected as a family, same as segmentation and baskets.

**Restraint.** A materiality floor, a minimum revenue share before a product
gets its own alert, a hard cap per run, consecutive periods collapsed into one
alert rather than one per month, and a stable dedupe key so an event fires
once. On a quiet business the output is zero alerts, and there is a test for it.

### The digest

A "business in review" email that reads in under a minute: the largest movement
first, two supporting lines, new alerts, and one good thing where there is one.
A weekly email that is only bad news gets filtered, and then the bad news stops
arriving too. An empty digest is not sent at all - sending nothing trains people
to ignore the ones that say something.

Delivery is pluggable and defaults to writing to the log, so the digest is fully
buildable and testable with no email account. Plain SMTP when configured, chosen
over a vendor SDK for the same reason the LLM provider is a raw POST.

### The scheduler

A free Render web service spins down after inactivity and cannot host a
reliable timer, so the trigger lives outside the app: GitHub Actions on a cron
hitting `POST /internal/tick` with a shared secret
(`.github/workflows/scheduled-refresh.yml`). If no token is configured the
endpoint returns 503 rather than sitting open.

Two secrets are needed in the repo before it does anything:
`BUSYLAB_API_URL` and `BUSYLAB_SCHEDULER_TOKEN`.

## Goal tracking

The one place the user supplies something rather than reading a result: a
metric, an amount and a window. Everything after that is computed.

Spec Pillar 4's example sets the bar - *"you will reach 87 percent of your Q1
target, and the gap is entirely Product 4's decline"* - and both halves matter.
A percentage tells the owner they have a problem; the attribution tells them
where it lives. The gap is decomposed into per-product changes that sum to it,
rather than naming a plausible culprit.

Three details:

- **The projection carries a band**, reusing the ARIMA machinery. That lets the
  UI distinguish "you will miss this" from "you might miss this", which a single
  projected number cannot express.
- **"Now" is the last sale in the file**, never today. A file uploaded three
  weeks late must not report three weeks of zero sales as a collapse in pace.
- **A closed window is not projected.** It reports what actually happened. A
  window barely begun reports nothing at all rather than extrapolating from a
  few days.

Goals live in the API (`POST /datasets/{id}/goals`) and are validated through
the engine's own model, so the API cannot accept a goal the engine would reject.
Setting one queues a re-analysis, and the target appears in the story like any
other finding.

## Customer intelligence

Requires a customer id, and nothing here runs without one. Four questions
(spec Pillar 3), each answering something a revenue total cannot:

**Repeat versus new.** Two businesses with identical revenue curves need
completely different things depending on whether the change is in who arrives
or who comes back. Only reported when the two halves genuinely diverge.

**RFM segmentation.** Scores are quartiles *within this business*, not fixed
thresholds - a naira figure that means "big spender" in one shop is a rounding
error in another. Recency is measured against the last sale in the file rather
than today, so a file from last year does not report every customer as lost.

**Cohort retention.** Each age is averaged only over cohorts old enough to have
reached it, because a cohort one month old has not failed to survive twelve
months. Cohorts too small to read are excluded rather than averaged in.

**Basket analysis** is the dangerous one, and gets the same treatment as
segmentation. Every product pair is a comparison, so a thirty-product catalogue
is 435 tests. Lift is FDR-corrected across the whole family and floored at a
minimum number of baskets, because a huge multiple on two co-occurrences means
nothing. The finding also states what counted as a basket - one order, or one
customer on one day - since those are different claims.

## Forecasting

ARIMA only (spec Pillar 1's model policy): light, interpretable, and it runs on
a CPU-only laptop and a small instance. Deep models are parked until there is a
clear accuracy gap and a compute budget. Order is chosen by **AICc** over a
bounded grid - the small-sample correction matters, because on seventeen
monthly points plain AIC will pick a five-parameter model that has fitted the
alternating noise as a cycle and will then forecast that imaginary cycle
forward with confident narrow bands.

Four rules keep a forecast honest:

- **Bands are mandatory**, 80% and 95%. A single projected number invites a
  business to plan against precision that does not exist.
- **A direction is claimed only when the band supports it, across the whole
  horizon.** If the interval still contains where the business is now, the
  answer is "holds roughly where it is". An oscillating fit can land its last
  step on a peak, so "heading up" has to mean consistently up rather than up
  on the month we happened to stop at.
- **The forecast reports its own accuracy.** The model is refit without the
  last few periods and scored against what actually happened. A model that
  could not predict the past does not get to assert the future.
- **Bounds are respected.** ARIMA is unbounded, so a steep decline happily
  projects revenue through zero; revenue is floored at zero. Profit is not,
  because going below zero is precisely the break-even finding.

A partial final period is dropped before fitting. A file exported on the 23rd
has a two-thirds month on the end that looks exactly like a collapse, and
anything fitted through it then "recovers" from a crash that never happened.

## The data quality gate

Automated ingestion means bad data arrives automatically too, and nobody is
watching (spec 4.3). A refresh with half-filled rows or a duplicated month
poisons the analysis, and poisoned output looks exactly as confident as good
output. So every ingest passes a gate **before** any finding is published, and
a failure holds the analysis rather than publishing something wrong.

Checked: duplicate rows, duplicated periods, null spikes in required columns,
date gaps mid-history, future and impossible dates, mostly-negative or
mostly-zero values, and - against the last run that passed - row count
collapse, distribution shift, and history going backwards.

The case it exists for: a partial re-export is **indistinguishable** from a
business losing two thirds of its sales. Given a baseline, the gate says so:

```
[block] This refresh has 67% fewer rows than last time
        984 rows now versus 2,954 before. A partial export looks exactly
        like a collapse in sales.
[block] This refresh ends earlier than the last one
        Newest row is 2024-06-06 but the previous run reached 2025-06-23.
```

Two deliberate calls. **Proportionality**: only genuinely poisonous problems
block; a 30% row drop or a handful of refunds warns and publishes, because a
gate that stops at the first imperfection is a gate nobody leaves switched on.
**No guessing without a baseline**: on a first upload the history checks do not
run at all rather than inventing a comparison.

## Narration and routing

The model writes sentences and picks which analysis answers a question. It
never computes, never decides and never produces a number (spec 2). Everything
degrades cleanly to deterministic behaviour, so **BusyLab is fully usable with
no API key** - the numbers and findings are identical, only the prose is
plainer.

```bash
# Optional. Without it, the engine's own wording is used.
export GROQ_API_KEY=...            # free tier
export BUSYLAB_LLM_PROVIDER=none   # or force it off entirely

./.venv/Scripts/python.exe -m busylab samples/fern_and_flame.xlsx \
    --ask "why did revenue drop, was it online?"
```

Model choice: **Groq free tier**, `llama-3.3-70b-versatile` for narration
(better prose, and it is cached so volume stays low) and
`llama-3.1-8b-instant` for routing (classification in an interactive path, so
latency wins). The endpoint is OpenAI-compatible and called with `urllib` from
the standard library, so swapping providers is configuration and the engine
gains no dependencies. Groq's binding free-tier limit is tokens per minute, so
narration sends one small cached call per finding rather than one large batch.

### Two guardrails, both enforced in tests

- **The model may not invent a number.** Every numeric token it writes must
  correspond to a value in `finding.facts`, allowing for reasonable renderings
  (`0.18` may be written `18%`, `551500` may be written `551.5k`). A sentence
  claiming an 23% fall when the facts say 18% is discarded and the engine's own
  wording is used. A model that computes has broken the one rule the whole
  architecture rests on.
- **The model may not give advice.** The same non-directive guard runs over its
  output as over the engine's own summaries.

### Routing stays on rails

A free-form question is matched to one of twelve named analyses. The model does
classification, which small fast models do reliably; it never analyses, which
they do not. A route it invents is discarded, keyword matching covers the
no-model case, and an unmatched question is refused with suggestions rather
than answered with a guess. Chips are only offered when the engine can actually
answer them.

## The web app

```bash
cd web && npm install && npm run dev     # http://localhost:3000
```

Next.js App Router, Recharts for standard shapes, hand-rolled SVG for the
waterfall and the correlation heatmap, Framer Motion for entrances.

It is a story, not a dashboard (spec 6). There is no chart picker, no axis
selector and no filter panel anywhere in the app on purpose: the user reads a
result the engine ranked and the design laid out. Interactivity only ever goes
*deeper into* findings that already exist - guided chips, a question box that
routes to a pre-built analysis, and an evidence panel on every card.

The chart is chosen by `finding.chart`, which the engine set. The frontend
switches on it and decides nothing.

Screens built: landing, upload, column check, analysing, story, drill-down.
Forecast, customers, goals and alerts are deliberately absent - there is no
engine behind them yet (build steps 7 to 9), and a screen showing invented
numbers would undo the point of the whole product.

## The API

A separate `api/` package that imports `busylab`. The dependency only ever
points that way, which is what keeps the engine shippable on its own - there is
a test that asserts importing the engine never pulls in FastAPI.

```bash
./.venv/Scripts/python.exe -m uvicorn api.main:app --reload --port 8000
# http://localhost:8000/docs
```

Analysis does not run inside the request. An upload returns a job id
immediately, a worker processes it off the request cycle, and the frontend
polls - spec 9 calls this architectural rather than cosmetic, and nothing
proactive can exist without it.

| Endpoint | Purpose |
|---|---|
| `POST /uploads` | Accept a spreadsheet, queue detection, return a job id |
| `GET /jobs/{id}` | Poll status and step; carries the result when done |
| `GET /datasets/{id}/columns` | What was understood, what still needs asking |
| `POST /datasets/{id}/columns` | Submit answers, queue the analysis |
| `GET /datasets/{id}/story` | The ranked narrative, each finding with its chart |
| `POST /datasets/{id}/ask` | Route a question to an already-computed analysis |
| `DELETE /datasets/{id}` | Remove an upload and its raw file |

Jobs and datasets live in SQLite locally. Spec 8 calls for a Postgres jobs
table with polling because it is one fewer service to run, and the interface
here is deliberately the small intersection of what SQLite and Postgres both do
well, so moving to Supabase is a driver swap rather than a redesign.
`claim()` is a locked read-then-conditional-update, which becomes
`SELECT … FOR UPDATE SKIP LOCKED` in Postgres.

Mapping memory (spec 4.1) is wired in: a confirmed set of role assignments is
stored against the schema fingerprint, so re-uploading the same shape of file
runs silently instead of asking the same questions again.

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
  narration/
    provider.py       pluggable LLM, Groq free tier, null by default
    narrate.py        facts to English, with the invented-number guard
    routing.py        question to pre-built analysis, plus follow-up chips
  quality.py          the data quality gate
  cli.py              python -m busylab <file> [--ask "..."]

api/
  jobs.py             job queue and dataset store, SQLite behind a
                      Postgres-shaped interface
  handlers.py         the only seam between the web layer and the engine
  main.py             FastAPI app

web/
  app/                landing, upload, and the dataset flow
  components/
    charts/Chart.tsx  one chart per finding type, chosen by the engine
    FindingCard.tsx   chart + sentence + evidence panel
    ColumnCheck.tsx   asks only about ambiguous columns
    StoryView.tsx     ranked narrative and drill-down
    QualityHold.tsx   shown when the gate holds an analysis
  lib/                API client, types, formatting

tests/
  fixtures.py         messy workbooks, planted truths, flat control
  test_detection.py   three-layer detection and the loader
  test_analysis.py    planted truths found, nothing invented on quiet data
  test_narration.py   the two model guardrails
  test_quality.py     the gate
  test_api.py         the flow a UI drives, over HTTP
```

## Testing approach

Two fixtures carry most of the weight, and they exist because a test that only
checks the engine agrees with itself proves nothing:

- **`planted_business`** has known effects deliberately put into it: a real
  decline from month 7 driven by the online channel, a best seller that is not
  the best earner, profit concentrated in one product, one product sold at a
  genuine loss, and a `salesperson` column that is pure noise. Every planted
  truth must be found; the noise must stay silent.
- **`flat_business`** has nothing in it at all. The engine must return **no**
  significant findings. This is the control that catches an engine tuned to
  always have something to say.

## Licence

Not yet licensed. All rights reserved.
