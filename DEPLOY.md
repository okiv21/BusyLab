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
2. **Project Settings → Database → Connection string → URI.** Copy it. It looks
   like:
   ```
   postgresql://postgres.abcdef:PASSWORD@aws-0-eu-west-2.pooler.supabase.com:6543/postgres
   ```
   Use the **connection pooler** URI (port 6543), not the direct one. A free
   Postgres has a low connection cap and two Render services plus the pooler is
   more forgiving.
3. **Storage → New bucket → `uploads`.** Keep it **private**. Uploads go
   through the API using the service key; the browser never touches the bucket.
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
   | `BUSYLAB_CORS` | your Vercel URL, e.g. `https://busylab.vercel.app` — no trailing slash |
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
   | `NEXT_PUBLIC_API` | `https://busylab-api.onrender.com` — no trailing slash |

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

## Known limits of the free tier

- **Cold starts.** The API sleeps after ~15 minutes idle and takes 30-60
  seconds to wake. The scheduled workflow allows for this with a retry.
- **The worker sleeps too.** A free background worker is not guaranteed to be
  always-on, so a job queued while it is down waits until it wakes. Nothing is
  lost - the job sits in Postgres - but it is not instant.
- **Connection cap.** The pool is capped at 4 per service. Raise
  `BUSYLAB_PG_POOL` only if you also raise the Supabase plan.
