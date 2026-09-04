# Setup guide

Long-form version of the README's Quick start, with the two parts that need more than one line: the Microsoft Ads goal setup and the scheduled import. Read this once, top to bottom, the first time you set this up.

## 1. Deploy the Cloudflare Worker

Terminal, inside the `worker/` folder. Full instructions, including what every `wrangler.toml` value means: [`worker/README.md`](../worker/README.md).

Short version: copy `wrangler.toml.example` to `wrangler.toml` and fill in your Cloudflare account ID and the subdomain you want the Worker to answer on; create a KV (Cloudflare's key-value store) namespace and paste its ID in; set four secrets (`CAPTURE_BEARER`, `OCI_BEARER`, `FILE_USER`, `FILE_PASS`); deploy. Keep the Worker's URL and the engine/file secret values handy — you'll need them in steps 4 and 7 below.

## 2. Register a ServiceTitan API application

Browser, at developer.servicetitan.io. Create an application with **read-only** scopes for:

- **CRM** — bookings, customers, contacts
- **JPM** (Job & Project Management) — jobs
- **Accounting** — invoices
- **Telecom** — calls
- **Marketing** — campaigns

Note the client ID, client secret, and app key (ServiceTitan calls this the "ST-App-Key") — they go in `.env` in step 4.

## 3. Add the beacon to your site

Your website's HTML template, or a tag manager (e.g. Google Tag Manager). Paste the snippet from [`worker/beacon.js`](../worker/beacon.js), to remember the click cookie. Connect your verified booking server to the authenticated capture endpoint using `CAPTURE_BEARER`; never place that secret in the snippet. See [`docs/capture.md`](capture.md) for what this does and how the matching works.

## 4. Local config files

Terminal, in the repo root:

```bash
cp accounts.yaml.example accounts.yaml
cp .env.example .env
```

Edit `accounts.yaml`: your ServiceTitan tenant ID, your campaign category (the ServiceTitan campaign category that marks Microsoft Ads traffic), your Worker's URL, and — once you've done step 5 below — your three goal names.

Edit `.env`: the ServiceTitan credentials from step 2, and `OCI_WORKER_BEARER` (the same value you set as the Worker's `OCI_BEARER` secret in step 1).

Every key is documented in the README's [Config reference](../README.md#config-reference) section.

## 5. Create the three Microsoft Ads offline-conversion goals

Microsoft Ads web UI, under **Tools > Offline conversions > Conversion Goals** (Microsoft moves its own menu items occasionally — search for "Offline conversions" under Tools if this has shifted). Create one goal per row:

| `accounts.yaml` key | Suggested name (anything works, as long as it matches `accounts.yaml` exactly) | Fires when |
|---|---|---|
| `completed_jobs` | ServiceTitan Completed Jobs - MS | A ServiceTitan job on a Microsoft-attributed campaign is completed and invoiced. Carries a dollar value. |
| `booked_web` | ServiceTitan Booked Job (Website) - MS | A ServiceTitan booking from a web/form submission, on a Microsoft-attributed campaign, is created. No value — a lead-quality signal. |
| `booked_call` | ServiceTitan Booked Job (Call) - MS | A ServiceTitan booking from a phone call, on a Microsoft-attributed campaign, is created. No value. |

For each goal:

- **Conversion window: 90 days** (Microsoft's maximum). A ServiceTitan job can complete weeks after the ad click that led to it, and this tool does not itself filter uploads by age — a shorter window just means Microsoft silently rejects real, valid conversions as too old.
- **Count: every conversion**, not just the first. Microsoft's alternative ("Unique" / first-only) setting would silently discard a second job from a repeat customer.
- **Revenue**: for `completed_jobs`, enable revenue tracking so Microsoft uses the value in each upload row (this tool sends the job's real invoice total). The two booked-job goals carry no value — leave revenue off for those.
- **Goal category**: whatever you use for your own reporting; it has no effect on this tool.

The name you give each goal must be typed **exactly** into `accounts.yaml`'s `microsoft_ads.goal_names` — Microsoft matches an uploaded row to a goal by name, and a mismatch is a silent 0% match rate on that goal, not an error.

## 6. First pull and build

Terminal, in the repo root. Run these once by hand to confirm everything is wired up before scheduling them:

```bash
st-msads-oci pull
st-msads-oci build --input output/input-latest.json
```

`pull` writes `output/input-latest.json` from ServiceTitan; `build` reads it, joins rows to captured click IDs, writes `output/summary-latest.json`, and publishes the two CSVs to the Worker. Check the summary — see [Reading results](../README.md#reading-results) in the README — before moving on.

## 7. Set up the Microsoft scheduled import

Microsoft Ads web UI, under **Tools > Offline conversions > Uploads tab > Schedule tab > + Schedule**. Create two schedules — one per file, since the click-id and hashed-PII files are two different tiers of the same run (tier A / tier B, explained in the README's [Reading results](../README.md#reading-results)):

| | Schedule 1 | Schedule 2 |
|---|---|---|
| Import type | Import from URL | Import from URL |
| URL | `https://<your-worker-domain>/f/oci-clickid.csv` | `https://<your-worker-domain>/f/oci-pii.csv` |
| Authentication | Basic | Basic |
| Username / password | the `FILE_USER` / `FILE_PASS` secrets from step 1 | the `FILE_USER` / `FILE_PASS` secrets from step 1 |
| Frequency | Daily | Daily |

Save both. From here on Microsoft pulls on its own schedule — there's nothing to upload by hand.

## 8. Confirm it's working

- `output/summary-latest.json` should show a non-zero `tier_a` or `tier_b` count once you have real bookings/jobs on the configured campaign category.
- Give it a day or two for Microsoft's first scheduled pull, then check the goal's conversion count in Microsoft Ads — using a report date range that covers when the underlying ad clicks happened, not just "today". See [`docs/click-date-credit.md`](click-date-credit.md) for why that matters.
- Download Microsoft's offline-conversion result file (from the same Uploads area) into `results/inbox/`, or just your normal Downloads folder, and run `st-msads-oci build` again — it gets parsed automatically and turns into a row in `match_rate_history.csv`.

For an existing installation, complete [the coordinated security upgrade](security-upgrade.md) before resuming scheduled imports.
