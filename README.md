# ServiceTitan → Microsoft Ads Offline Conversion Uploader

**Unofficial. Not affiliated with, endorsed by, or sponsored by ServiceTitan or Microsoft.** This is an independent, third-party tool that integrates with the ServiceTitan and Microsoft Advertising APIs through their public interfaces. "ServiceTitan," "Microsoft Advertising," "Bing," and other product names are trademarks of their respective owners.

**ServiceTitan already sends your closed-job revenue back to Google Ads. It sends Microsoft/Bing nothing.**

If you run Microsoft Ads for a ServiceTitan shop, Microsoft's automated bidding is flying blind: it never learns which ad clicks became booked, paid jobs, so it optimizes toward raw form fills instead of revenue. This tool closes that gap. It matches your ServiceTitan booked and completed jobs back to the Microsoft ad click that produced them and uploads them as offline conversions, so Microsoft bids toward what actually closes. Built for ServiceTitan home-services shops spending on Microsoft/Bing Ads.

## What it does

`servicetitan-msads-oci` sends your ServiceTitan booked and completed jobs to Microsoft Advertising as offline conversions, matched back to the ad click that produced them. A small Cloudflare Worker (a script that runs on Cloudflare's edge network, not your own server) captures each visitor's Microsoft Click ID (`msclkid`) when they land on your site from an ad, and a scheduled Python pull later joins that click ID to whatever ServiceTitan booking, completed job, or booked call it produced. Microsoft uses the result to credit the conversion back to the original ad and to feed value-based Smart Bidding (Microsoft's automated bidding that optimizes toward conversion value, not just conversion count).

**Flow:** ad click -> Worker stores msclkid -> (days later) ST job booked and completed -> Python pull -> engine joins and builds CSV -> Worker hosts the file -> Microsoft scheduled import pulls -> conversion credited -> triage reports match rate.

## Why this exists

ServiceTitan's own Marketing Pro / Ads Optimizer already imports completed-job revenue back to an ad platform — but only to Google, matched by `gclid` (Google's click ID) or by phone call. It does not touch Microsoft/Bing at all. That gap is the reason this repo exists: without it, a ServiceTitan shop running Microsoft Ads has no automated way to tell Microsoft which ad clicks turned into real, paid jobs, so Microsoft's bidding optimizes blind to what actually closed.

Sources: [How revenue import works in Ads Optimizer](https://help.servicetitan.com/v1/docs/how-revenue-import-works-in-ads-optimizer), [ServiceTitan Ads Optimizer feature page](https://servicetitan.com/features/pro/marketing/ads).

## Honest prerequisites

This is real setup work, not a five-minute install. Budget time for a one-time Cloudflare deploy, a ServiceTitan API app registration, and Microsoft goal setup — the ongoing work after that is automated.

- A Cloudflare account (the free plan works; the optional per-IP rate limit on the capture endpoint needs a paid Workers plan — see `worker/README.md`).
- A ServiceTitan API application (register at developer.servicetitan.io) with read scopes for **CRM**, **JPM** (Job & Project Management), **Accounting**, **Telecom**, and **Marketing**.
- A Microsoft Ads account with the three offline-conversion goals created and a scheduled file import configured — `docs/setup.md` walks through both.
- Python 3.11 or newer.
- Node.js 20 or newer — only needed once, to deploy the Cloudflare Worker.

## Quick start

1. **Terminal, in the repo root** — install the package:
   ```bash
   pip install -e .
   ```
2. **Terminal, inside the `worker/` folder** — deploy the Cloudflare Worker that captures click IDs and hosts the two upload files. Full steps: [`worker/README.md`](worker/README.md).
3. **Your website's HTML, or a tag manager (e.g. Google Tag Manager)** — paste the reference snippet from [`worker/beacon.js`](worker/beacon.js), with its `WORKER` constant changed to your deployed Worker's URL. See [`docs/capture.md`](docs/capture.md) for what it does.
4. **Terminal, in the repo root** — create your local config files from the templates, then edit both with your values (see Config reference below):
   ```bash
   cp accounts.yaml.example accounts.yaml
   cp .env.example .env
   ```
5. **Terminal, in the repo root** — pull fresh data from ServiceTitan:
   ```bash
   st-msads-oci pull
   ```
6. **Terminal, in the repo root** — build the two conversion files and publish them to the Worker:
   ```bash
   st-msads-oci build --input output/input-latest.json
   ```
7. **Microsoft Ads web UI** — before this step, create the three offline-conversion goals ([`docs/setup.md`](docs/setup.md) has the exact settings). Then set up a daily scheduled import pointing at `https://<your-worker>/f/oci-clickid.csv`, and a second one at `https://<your-worker>/f/oci-pii.csv`, both with authentication set to Basic using the `FILE_USER` / `FILE_PASS` values you set on the Worker in step 2.

## Scheduling

Run the pull and the build back to back, at least once a day — Microsoft's scheduled import also runs daily, so running more often than that gains nothing (though nothing breaks if you do). A failed run notifies you automatically: stdout always, plus whatever's configured under `notifications` in `accounts.yaml`.

**cron** — Terminal, `crontab -e`. cron doesn't load your shell profile, so use full paths to your Python environment's `st-msads-oci`, not just the bare command:
```cron
0 6 * * * cd /path/to/servicetitan-msads-oci && /path/to/venv/bin/st-msads-oci pull && /path/to/venv/bin/st-msads-oci build --input output/input-latest.json >> logs/oci.log 2>&1
```

**launchd** (macOS) — Terminal. Save the file below as `~/Library/LaunchAgents/com.servicetitan-msads-oci.plist`, then run `launchctl load ~/Library/LaunchAgents/com.servicetitan-msads-oci.plist`:
```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.servicetitan-msads-oci</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string>
    <string>-c</string>
    <string>cd /path/to/servicetitan-msads-oci && /path/to/venv/bin/st-msads-oci pull && /path/to/venv/bin/st-msads-oci build --input output/input-latest.json</string>
  </array>
  <key>StartCalendarInterval</key>
  <dict><key>Hour</key><integer>6</integer><key>Minute</key><integer>0</integer></dict>
  <key>StandardOutPath</key><string>/path/to/servicetitan-msads-oci/logs/oci.log</string>
  <key>StandardErrorPath</key><string>/path/to/servicetitan-msads-oci/logs/oci.log</string>
</dict>
</plist>
```

## Reading results

- `output/summary-latest.json` — written on every run: tier A (click-id matched) and tier B (hashed-PII only) row counts, withheld count, capture rate, dropped-row count, triage/project finding counts, notifier errors, and whether publish to the Worker succeeded.
- `match_rate_history.csv` — one row per Microsoft result file processed, appended by triage: how many rows were uploaded vs. how many Microsoft reports as `Success`, broken out by goal and campaign type (Search vs. PMax).
- `results/inbox/` — drop Microsoft's downloaded offline-conversion result files here (or just leave them in your default Downloads folder — triage checks both). The next `st-msads-oci build` run parses them, updates `match_rate_history.csv`, and archives the file to `results/archive/`.

## Gotchas

- **Conversions show up under the click date, not the upload date.** A short report lookback window in Microsoft Ads can read zero even though your uploads are working fine. See [`docs/click-date-credit.md`](docs/click-date-credit.md).
- **The served CSV is a rolling 14-day file, not just today's new rows.** This is deliberate. See [`docs/rolling-file.md`](docs/rolling-file.md) for why, and what it protects against.
- Rows matching `test_identities` in `accounts.yaml` are silently dropped before upload — point them at your own test bookings so test traffic never reaches Microsoft.
- A row with no email, phone, or linked call is dropped outright, and ServiceTitan bookings/jobs/calls outside your configured `campaign_category` are never enriched with contact info in the first place, so they can never match.
- If the Worker is unreachable when a run starts, the run aborts before writing any CSV — a missed run is safer than a half-updated file.

## Config reference

### `accounts.yaml`

| Key | Meaning |
|---|---|
| `servicetitan.tenant_id` | Your ServiceTitan tenant ID. |
| `servicetitan.campaign_category` | The ServiceTitan campaign *category* name that marks a campaign as Microsoft Ads traffic (for example, `"Paid Microsoft"`). Only bookings/jobs/calls on a matching campaign get uploaded or enriched with contact info. Required. |
| `microsoft_ads.goal_names.completed_jobs` | Must exactly match your "completed job" offline-conversion goal name in Microsoft Ads. |
| `microsoft_ads.goal_names.booked_web` | Must exactly match your "booked job, from a web/form booking" goal name. |
| `microsoft_ads.goal_names.booked_call` | Must exactly match your "booked job, from a call" goal name. |
| `microsoft_ads.legacy_goal_names` | Optional list of older goal names your Microsoft result files might still reference, so triage can still match them to ledger rows. |
| `worker.url` | Your deployed Cloudflare Worker's URL. |
| `initial_watermark` | UTC ISO timestamp. The first pull only looks for ServiceTitan data from this instant forward — keep it inside Microsoft's goal click window (up to 90 days) or those conversions will be rejected as too old. |
| `test_identities.emails`, `test_identities.phones` | Your own test bookings' emails/phones. Matching rows are dropped before upload. |
| `notifications` | Optional list of notifier configs. Each entry needs a `type` (`webhook`, `smtp`, or `graph`); secrets for whichever type you use come from `.env`. |

### `.env`

| Variable | Meaning |
|---|---|
| `ST_CLIENT_ID`, `ST_CLIENT_SECRET`, `ST_APP_KEY` | Your ServiceTitan API application's credentials. Read-only scopes are enough. |
| `OCI_WORKER_BEARER` | Bearer token the engine uses to call the Worker's `/map` and `/f/...` endpoints — must match the `OCI_BEARER` secret you set on the Worker with `wrangler secret put`. |
| `NOTIFY_WEBHOOK_URL` | Slack/Teams incoming webhook URL. Only used if `accounts.yaml` has a `notifications` entry with `type: webhook`. |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_FROM`, `SMTP_TO` | Any SMTP server. `SMTP_PORT` defaults to 587. Only used with a `type: smtp` notifier. |
| `GRAPH_TENANT_ID`, `GRAPH_CLIENT_ID`, `GRAPH_CLIENT_SECRET`, `GRAPH_SENDER`, `GRAPH_TO` | Microsoft 365 Graph app credentials, for run-summary emails via Graph's `sendMail`. Only used with a `type: graph` notifier. |

## Roadmap

Out of scope for v1, and candidates for v2, ordered by how much each would widen adoption:

1. User-defined conversion types — today the three goal types (`completed_jobs`, `booked_web`, `booked_call`) are hardcoded in the engine; letting `accounts.yaml` declare additional goal types is real backlog, not v1 scope. This is the biggest blocker to a shop whose Microsoft goals differ from the built-in three adopting the tool as-is.
2. A first-class ServiceTitan client — the bundled pull (`pull/pull_servicetitan.py`) is a reference implementation, not hardened for every tenant configuration. Hardening it moves the pull from "a developer can adapt this" to "a shop runs it as-is."
3. Direct Microsoft API push, removing the scheduled-import setup step and the need to host the two files — a simpler one-time setup and near-real-time uploads.
4. An alternate capture path that writes `msclkid` directly into a ServiceTitan field, letting a shop drop Cloudflare from the stack. Together with the direct API push above, it removes the Cloudflare Worker requirement completely.
5. Project rollup + suspect recovery — recovering jobs whose attribution lives on a parent project rather than the job itself. Already has an extension point in the input contract (`project_jobs` — see `pull/INPUT_FORMAT.md`), just not populated by the bundled pull.
6. Google web-originated closed-won revenue, as a separate tool (ServiceTitan's own Marketing Pro already covers Google calls; the gap there is web).

## License

MIT — see [`LICENSE`](LICENSE).
