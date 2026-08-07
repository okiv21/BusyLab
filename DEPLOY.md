# Deploying BusyLab

Three free services, in the order the spec lays out (section 9): Supabase for
Postgres and file storage, Render for the API and worker, Vercel for the
frontend. GitHub Actions triggers the scheduled work.

Everything here needs accounts and sign-ins, so these are steps for you to run.
The repository is already configured for all of it.

---

## Why not just SQLite and a folder

Because a free Render instance has an **ephemeral disk**. It spins down after
inactivity and comes back with the filesystem empty. Uploads, jobs, goals,
alerts and mapping memory would all disappear with no error anywhere - the app
would look like it was working and quietly forget everything overnight.

That is why the store is Postgres and the files are Supabase Storage. Locally
both fall back to SQLite and a folder, which is fine because nothing is lost if
your laptop restarts.

---

## 1. Supabase (database + file storage)

1. Create a project at [supabase.com](https://supabase.com). Note the database
   password you set - it goes in the connection string.
2. Click **Connect** at the **top of the dashboard** - next to the project
   name, not under Settings. You will be offered three connection strings:

   | Option | Port | Use this? |
   |---|---|---|
   | Direct connection | 5432 | **No.** It is IPv6-only, and Render is not. |
   | **Transaction pooler** | **6543** | **Yes.** |
   | Session pooler | 5432 | Fallback if the pooler misbehaves. |

   Copy the **Transaction pooler** URI. It looks like:
   ```
   postgresql://postgres.abcdefgh:[YOUR-PASSWORD]@aws-0-eu-west-2.pooler.supabase.com:6543/postgres
   ```
   Replace `[YOUR-PASSWORD]` with the database password you set when creating
   the project. If you have lost it, reset it under
   **Settings → Database → Database password**.

   > The pooler is not just a preference. A free Postgres has a low connection
   > cap, and two Render services reconnecting after every spin-down will
   > exhaust it. The code disables psycopg's automatic prepared statements for
   > this reason too - the transaction pooler does not support them, and
   > psycopg turns them on by itself after a query has run five times.
3. **Storage - New bucket - `uploads`.** Three options appear:

   | Option | Set it to | Why |
   |---|---|---|
   | Public bucket | **off** | These are a business's actual sales records. Public means anyone with the URL can read them. Uploads go through the API using the service key, so the browser never needs access. |
   | Restrict file size | **25 MB** | Matches the API's own cap, so the limit still holds if anything ever reaches the bucket another way. |
   | Restrict MIME types | optional, see below | Safe to switch on. |

   If you do restrict MIME types, these are the five the API sends - one per
   accepted extension:

   ```
   application/vnd.openxmlformats-officedocument.spreadsheetml.sheet
   application/vnd.ms-excel.sheet.macroEnabled.12
   application/vnd.ms-excel
   text/csv
   text/tab-separated-values
   ```

   Miss one and uploads of that format fail with a bucket error rather than
   anything BusyLab can explain, so leaving the restriction off is a reasonable
   choice too - the API already refuses anything that is not a spreadsheet.

   > **The name you give the bucket is the name you must configure.** It goes
   > in `SUPABASE_BUCKET`, and it is case-sensitive: `Busylab` and `busylab`
   > are different buckets. Supabase reports a name it does not recognise with
   > the same `NoSuchBucket` it uses for a bucket that was never created, so a
   > capital letter in the wrong place looks exactly like having skipped this
   > step. `check_storage.py` lists the real names for this reason.
   >
   > Note also that Storage has an **S3 Access Keys** feature. That is a
   > different way of connecting and BusyLab does not use it - you need a
   > bucket, not an access key.
4. **Project Settings → API.** Copy the **Project URL** and the
   **`service_role`** key.

> The `service_role` key bypasses row-level security. It belongs on the server
> only, never in the frontend or in this repository.

Tables are created automatically on first start, so there is no migration to
run.

### Check both before going further

Neither the database nor the bucket can be exercised locally, so without this
step the first thing to find a wrong key or a missing bucket is a real upload,
several minutes after a deploy, reported as a bare `400`. Two scripts close
that loop in seconds:

```bash
python check_db.py
```

Paste the pooler URI **with `[YOUR-PASSWORD]` still in it** and type the
password at the second prompt. It is inserted and percent-encoded for you,
which matters more than it sounds: a `#`, `?` or `/` in a password is not
rejected by a URL parser, it silently truncates the password, and the database
then reports authentication failure for a password that is correct. Typing it
separately also keeps it out of your shell history and away from PowerShell,
which expands `$` and eats backticks.

On success it offers to save the working string to `.env` (gitignored) and to
run the full store contract - every query the application makes, against the
real database.

```bash
python check_storage.py
```

Round-trips an actual spreadsheet through the bucket: upload, download, compare
the bytes, delete. It catches the two mistakes that authenticate perfectly and
then fail - using the **anon** key, which cannot write, and a MIME restriction
missing one of the five types above.

A third check comes in later, once the API is deployed - see the end of step 2.

## 2. Render (API + worker)

1. **New - Blueprint**, point it at this repository. Render reads
   `render.yaml` and creates one service, `busylab-api`.

   > **Only one service, and that is deliberate.** Spec 9 wants the worker
   > separate so a heavy analysis cannot block an HTTP request, but Render has
   > no free instance type for background workers - they start at $7/month. So
   > the free configuration runs the worker in-process
   > (`BUSYLAB_INLINE_WORKER=1`) and `render.yaml` carries the separate service
   > commented out, ready to uncomment when the analyses outgrow a shared
   > instance.
   >
   > This works rather than merely compiles: the queue lives in Postgres, so
   > nothing is lost while the instance sleeps. The cron wakes it, the worker
   > thread starts with it, and it drains whatever accumulated. The cost is
   > that a large analysis competes for CPU with the API, which shows up as a
   > slow request rather than a failed one.
2. It will prompt for the secrets marked `sync: false`:

   | Variable | Value |
   |---|---|
   | `DATABASE_URL` | the Supabase pooler URI from step 1 |
   | `SUPABASE_URL` | the Supabase project URL |
   | `SUPABASE_SERVICE_KEY` | the `service_role` key |
   | `SUPABASE_BUCKET` | the bucket's name, exactly as Storage shows it |
   | `BUSYLAB_CORS` | your Vercel URL, e.g. `https://busylab.vercel.app` - no trailing slash |
   | `BUSYLAB_SCHEDULER_TOKEN` | a long random string you invent |

   Optional: `GROQ_API_KEY` for nicer wording, and the `SMTP_*` variables plus
   `BUSYLAB_DIGEST_TO` to actually send the digest. They go on this service
   because the worker runs here.

3. Wait for it to go green, then check it from outside:

   ```bash
   python check_api.py https://busylab-api.onrender.com
   ```

   Once the frontend is up, pass that too and it will check the browser will
   be allowed to call the API:

   ```bash
   python check_api.py https://busylab-api.onrender.com https://busylab.vercel.app
   ```

   It waits out a cold start rather than calling a sleeping service dead - the
   **first request after inactivity takes 30-60 seconds** on a free instance,
   which is the free tier and not a bug.

   Three things it will tell you that are otherwise genuinely hard to see:

   - **A wrong URL looks like a broken service.** Every name under
     `onrender.com` resolves whether or not the service exists, so a typo
     gives you a working host that 404s rather than a DNS failure.
   - **A refused origin is invisible from both ends.** The browser reports a
     generic network error - it will not admit the request was blocked - and
     the server logs an ordinary request, because from its side nothing went
     wrong. `/health` reports the allowed list so it can be compared directly.
   - **`storage: local` means uploads are being written to Render's disk**,
     which is erased on every restart. It deploys clean and works perfectly
     until the first spin-down, then silently loses everything.

   A service can show **Live** and still be failing every request: the job
   store is created lazily on the first request, not at startup, so a bad
   `DATABASE_URL` passes the deploy and the health check is where it surfaces.

## 3. Vercel (frontend)

1. **Add New → Project**, import this repository.
2. Set **Root Directory** to `web`. Vercel detects Next.js on its own.

   > **This one is not optional, and getting it wrong is not obvious.** The
   > repository root holds `api/`, which is Python. Vercel's zero-config treats
   > a top-level `api/` directory as serverless functions, so pointed at the
   > root it never sees the Next app at all - it builds `api/main.py` as a
   > Python lambda instead. That fails, because FastAPI with pandas, scipy and
   > statsmodels is neither a Vercel handler nor within the lambda size limit,
   > and the site answers every request with **"This Serverless Function has
   > crashed"**. Nothing in the message points at the root directory.
   >
   > If you see that page, this is why. Fix it under **Settings → Build and
   > Deployment → Root Directory**, then redeploy.
3. Add one environment variable:

   | Variable | Value |
   |---|---|
   | `NEXT_PUBLIC_API` | `https://busylab-api.onrender.com` - no trailing slash |

   > `NEXT_PUBLIC_*` variables are inlined at build time, not read at runtime.
   > So this must be set **before** the build that ships, and changing it later
   > needs a redeploy, not a restart. Forgetting it does not fail the build:
   > the site falls back to `127.0.0.1:8000` and tells every visitor the API is
   > not running. The frontend now detects that combination and says the
   > variable is missing instead.
4. Deploy. Then go back to Render and make sure `BUSYLAB_CORS` matches the
   Vercel URL exactly. A mismatch shows up as *"Cannot reach BusyLab"* in the
   browser while `curl` works perfectly, because `localhost` and `127.0.0.1`
   and `https://…` are all different origins to a browser.

## 4. Scheduled monitoring

`.github/workflows/scheduled-refresh.yml` runs Mondays at 07:00 UTC and on the
1st of each month. It needs two repository secrets:

**Settings → Secrets and variables → Actions → New repository secret**

| Secret | Value |
|---|---|
| `BUSYLAB_API_URL` | `https://busylab-api.onrender.com` |
| `BUSYLAB_SCHEDULER_TOKEN` | the same string you set on Render |

Test it with **Actions → Scheduled refresh → Run workflow**.

Without a token configured the endpoint returns 503 rather than sitting open,
so nothing is exposed while you are still setting up.

> A cron lives outside the app because a free Render service spins down and
> cannot host a reliable timer (spec 9). Move to Render Cron once you are on a
> paid instance anyway.

---

## 5. Email for the weekly digest (optional)

Without this the digest is written to the worker log instead of sent. That is
a supported state, not a broken one - everything else works, and the digest is
previewable in the app either way.

Two things are needed: an SMTP account, and somewhere to send it.

### Brevo (recommended once anyone but you receives these)

Free tier is 300 emails a day, no card, and it is a real relay rather than a
personal mailbox.

1. Create an account at [brevo.com](https://www.brevo.com).
2. Top-right user menu - **SMTP & API** - the **SMTP** tab.
3. Copy the **SMTP key**. It is *not* the API key; the two are on adjacent
   tabs and they are not interchangeable. Watch for a trailing space when
   copying - a single extra character fails authentication with a message that
   does not mention whitespace.
4. **Senders, Domains & Dedicated IPs - Senders - Add a sender**, and verify
   the address you will send *from*. Brevo emails it a confirmation link.
   Sending from an unverified address is rejected.

| Variable | Value |
|---|---|
| `SMTP_HOST` | `smtp-relay.brevo.com` |
| `SMTP_PORT` | `587` |
| `SMTP_USER` | the SMTP login Brevo shows you, which is an email address |
| `SMTP_PASSWORD` | the **SMTP key** |
| `SMTP_FROM` | a sender you verified in step 4 |

### About your domain

Verifying a single address gets you sending. It does not get you *delivered*.

Authenticating a **domain** - adding the SPF and DKIM records Brevo gives you
to your DNS - is what stops a meaningful share of these landing in spam, and it
is the difference between a digest a customer reads and one they never see. A
domain costs a few pounds a year and is the first thing in this project that is
not free.

Until then, sending to yourself and to people who are expecting it works fine.

### Gmail instead, for testing only

Quicker if you just want to see one arrive: enable 2-Step Verification, create
an app password at
[myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords),
then `smtp.gmail.com` on port `587` with that 16-character code as the
password. Roughly 500 a day.

Fine while it is only you. Not the right answer for customers: a personal
mailbox has no domain authentication behind it, and Gmail rate-limits in ways
that are invisible until they are not.

### Where the variables go

On the **worker** service in Render, not the API - the worker is what runs the
scheduled analysis and therefore what sends. Locally they go in `.env`.

### Who receives it

Each dataset carries its own address, so every business gets its own numbers:

```
PUT /datasets/{id}/recipient   {"email": "owner@theirshop.com"}
```

`BUSYLAB_DIGEST_TO` remains as a fallback for anything without one, which is
what a single-tenant setup looks like while you are testing against your own
data.

> **Before anyone else uses this:** there is no authentication yet, so anyone
> who knows a dataset id can change where its digest is sent. That is fine
> while it is your data on your machine and is not fine in public. Accounts
> need to come before customers do.

### Check it works, without waiting for Monday

Once the variables are on the worker and it has redeployed:

```bash
curl -X POST "https://busylab-api.onrender.com/internal/test-email?token=YOUR_SCHEDULER_TOKEN&to=you@example.com"
```

It sends one throwaway message and tells you what happened:

```json
{"sent": true, "mailer": "smtp", "to": "you@example.com",
 "hint": "Delivered. Check the inbox, and the spam folder."}
```

`"mailer": "log"` means no SMTP settings were found, so it was written to the
worker log rather than sent - check the variables are on the **worker**
service, not only the API.

`"sent": false` means the provider refused it. The worker log carries the
reason. The two usual causes are an unverified `SMTP_FROM`, and the API key
(`xkeysib-`) pasted where the SMTP key (`xsmtpsib-`) belongs.

### When it actually sends

Only on a **scheduled** run - the Monday and monthly cron. Re-analysing because
you set a goal or confirmed a column does not send anything, or you would get
an email every time you clicked. An empty digest is not sent at all.

Test the whole path without waiting for Monday:

**Actions - Scheduled refresh - Run workflow**

---

## Checking it worked

```bash
# 1. Awake and connected
curl https://busylab-api.onrender.com/health

# 2. Upload something real
curl -F "file=@your-sales.xlsx" https://busylab-api.onrender.com/uploads
```

Then open the Vercel URL and upload through the interface.

**The test that matters:** restart the Render services and reload. If your
dataset is still there, persistence is wired correctly. If it vanished,
`DATABASE_URL` did not reach the services and they fell back to SQLite on the
ephemeral disk.

---

## Costs, and where they bite first

Everything above is free. Spec 9 predicts the order in which that stops being
true:

1. **Worker memory**, as analyses get heavier. ARIMA on a large catalogue is
   the first thing to feel a 512MB instance.
2. **Supabase storage**, as raw uploads accumulate. There is a
   `DELETE /datasets/{id}` endpoint; a retention policy that drops the raw file
   after a successful ingest and keeps the parsed result is the cheap fix.
3. **Narration calls**, as usage grows. Already cached per finding, so a
   sentence is only paid for when the numbers behind it change.

## If the build fails

| What you see | What it means |
|---|---|
| `A free instance type is not available for services of type worker` | An older `render.yaml`. Background workers are paid-only on Render; pull the latest, which runs the worker in-process. |
| `No matching distribution found for pandas==...` | An older `requirements.txt` with exact pins resolved on a different OS and Python. The current file uses bounded ranges so pip picks whatever the target platform actually has. |
| `ModuleNotFoundError: No module named 'api'` | The service's root directory is set to something other than the repository root. The API lives at the top level; only the *frontend* uses a root of `web`. |
| Build succeeds, service will not start | Check the start command is `uvicorn api.main:app --host 0.0.0.0 --port $PORT`. Omitting `--host 0.0.0.0` binds to localhost and Render's health check never reaches it. |

## If the database will not connect

| What you see | What it means |
|---|---|
| `could not translate host name` | The URI was copied with `[YOUR-PASSWORD]` still in it, or a character in the password needs URL-encoding. `@ : / ?` in a password must be percent-encoded, or reset the password to something alphanumeric. |
| `Network is unreachable` | You used the **direct** connection (`db.xxx.supabase.co:5432`). It is IPv6-only and Render is not. Use the transaction pooler. |
| `prepared statement "_pg3_0" already exists` | Prepared statements reaching the pooler. The code disables them, so this means an older build is deployed - redeploy. |
| `too many connections` | Lower `BUSYLAB_PG_POOL`, or you are running more services than a free Postgres allows. |
| `password authentication failed for user "postgres"` | The **username** is wrong, not the password. On the pooler it must be `postgres.<your-project-ref>`; plain `postgres` belongs to the direct connection. This is the single most common failure, and the error points at the wrong thing. |
| `password authentication failed for user "postgres.abc..."` | Now it really is the password. Reset it under **Settings - Database**, or percent-encode any `@ : / ?` it contains. |
| `(ECIRCUITBREAKER) too many authentication failures` | Supabase has blocked the address after repeated failed logins. Fix the credentials, then **wait about five minutes** - it will reject a correct password until the block clears. |
| Data vanishes after a restart | `DATABASE_URL` never reached the service, so it silently fell back to SQLite on the disposable disk. Check the variable is set on **both** the API and the worker. |

To check the connection string before deploying anything, run it against the
contract suite - this is exactly what those 20 skipped tests are for:

```bash
TEST_DATABASE_URL="postgresql://postgres.xxx:PASSWORD@aws-0-...:6543/postgres"   python -m pytest tests/test_stores.py -q
```

All 46 passing means the Postgres path genuinely works. Do this before Render,
not after - it turns a confusing deploy failure into a clear local one.

## Known limits of the free tier

- **Cold starts.** The API sleeps after ~15 minutes idle and takes 30-60
  seconds to wake. The scheduled workflow allows for this with a retry.
- **The worker sleeps too.** A free background worker is not guaranteed to be
  always-on, so a job queued while it is down waits until it wakes. Nothing is
  lost - the job sits in Postgres - but it is not instant.
- **Connection cap.** The pool is capped at 4 per service. Raise
  `BUSYLAB_PG_POOL` only if you also raise the Supabase plan.
