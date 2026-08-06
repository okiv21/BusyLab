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
4. **Project Settings → API.** Copy the **Project URL** and the
   **`service_role`** key.

> The `service_role` key bypasses row-level security. It belongs on the server
> only, never in the frontend or in this repository.

Tables are created automatically on first start, so there is no migration to
run.

## 2. Render (API + worker)

1. **New → Blueprint**, point it at this repository. Render reads
   `render.yaml` and creates two services: `busylab-api` and `busylab-worker`.
2. It will prompt for the secrets marked `sync: false`. Set these on **both**
   services:

   | Variable | Value |
   |---|---|
   | `DATABASE_URL` | the Supabase pooler URI from step 1 |
   | `SUPABASE_URL` | the Supabase project URL |
   | `SUPABASE_SERVICE_KEY` | the `service_role` key |

   On **`busylab-api`** only:

   | Variable | Value |
   |---|---|
   | `BUSYLAB_CORS` | your Vercel URL, e.g. `https://busylab.vercel.app` - no trailing slash |
   | `BUSYLAB_SCHEDULER_TOKEN` | a long random string you invent |

   Optional on both: `GROQ_API_KEY` for nicer wording. Optional on the worker:
   `SMTP_HOST`, `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_FROM` to actually send the
   digest.

3. Wait for both to go green, then check:
   ```
   curl https://busylab-api.onrender.com/health
   ```
   Expect `{"ok":true,...}`. The **first request after inactivity takes 30-60
   seconds** while the free instance wakes up. That is the free tier, not a bug.

## 3. Vercel (frontend)

1. **Add New → Project**, import this repository.
2. Set **Root Directory** to `web`. Vercel detects Next.js on its own.
3. Add one environment variable:

   | Variable | Value |
   |---|---|
   | `NEXT_PUBLIC_API` | `https://busylab-api.onrender.com` - no trailing slash |

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

### Which provider

**Gmail app password** is the quickest for testing, since you already have the
account. Free, ~500 emails a day, working in two minutes.

1. Enable **2-Step Verification** on the Google account - app passwords do not
   exist without it.
2. Go to [myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords),
   create one named `BusyLab`, and copy the 16-character code.
3. Use it as `SMTP_PASSWORD`. It is not your Google password, and it can be
   revoked on its own.

| Variable | Value |
|---|---|
| `SMTP_HOST` | `smtp.gmail.com` |
| `SMTP_PORT` | `587` |
| `SMTP_USER` | your full Gmail address |
| `SMTP_PASSWORD` | the 16-character app password |
| `SMTP_FROM` | the same Gmail address |
| `BUSYLAB_DIGEST_TO` | where the digest should land |

**Brevo** is the better answer once anyone other than you is receiving these.
Free tier is 300 emails a day, no card, and it is a real relay rather than a
personal mailbox. Create an account, then **SMTP & API - SMTP**:
`smtp-relay.brevo.com`, port `587`, with the login and master password it
shows you.

> Sending to yourself from Gmail is fine. Sending to **customers** from a
> personal Gmail is not: without a domain and its SPF and DKIM records, a
> meaningful share of it lands in spam. That needs a domain, which is the
> point at which Brevo or Resend earns its place.

### Where the variables go

On the **worker** service in Render, not the API - the worker is what runs the
scheduled analysis and therefore what sends. Locally they go in `.env`.

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

## If the database will not connect

| What you see | What it means |
|---|---|
| `could not translate host name` | The URI was copied with `[YOUR-PASSWORD]` still in it, or a character in the password needs URL-encoding. `@ : / ?` in a password must be percent-encoded, or reset the password to something alphanumeric. |
| `Network is unreachable` | You used the **direct** connection (`db.xxx.supabase.co:5432`). It is IPv6-only and Render is not. Use the transaction pooler. |
| `prepared statement "_pg3_0" already exists` | Prepared statements reaching the pooler. The code disables them, so this means an older build is deployed - redeploy. |
| `too many connections` | Lower `BUSYLAB_PG_POOL`, or you are running more services than a free Postgres allows. |
| `password authentication failed` | The database password, not your Supabase account password. Reset it under **Settings - Database**. |
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
