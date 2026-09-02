# st-msads-oci-worker

Cloudflare Worker (a small script that runs on Cloudflare's edge network,
not on your own server) that captures Microsoft Ads click IDs (`msclkid`)
from your website and serves the two CSV files Microsoft Ads' offline
conversion import pulls on a schedule.

## Deploy

Run these commands in a Terminal, inside this `worker/` folder.

```bash
cp wrangler.toml.example wrangler.toml
```

Edit `wrangler.toml`:
- `account_id` — your Cloudflare account ID (Cloudflare dashboard, right sidebar).
- `routes` — the subdomain you want this Worker to answer on, e.g. `oci.yourdomain.com`.
- `[vars] ALLOWED_ORIGIN` — comma-separated list of the exact origins (scheme + host,
  e.g. `https://www.yourdomain.com`) allowed to call the `/c` beacon endpoint. This is
  a CORS allowlist, not a security boundary by itself — see below.
- `[[kv_namespaces]] id` — created in the next step.
- `[[unsafe.bindings]]` (rate limit) — only available on paid Workers plans; delete
  this whole block if you're on the free plan.

Create the KV namespace (KV is Cloudflare's key-value storage; this is where click
IDs and form submissions get parked until they're exported):

```bash
npx wrangler kv namespace create OCI
```

Paste the `id` it prints into `wrangler.toml`.

Set the secrets (values you type once; Cloudflare stores them encrypted, they never
appear in the repo or in `wrangler.toml`):

```bash
npx wrangler secret put OCI_BEARER
npx wrangler secret put FILE_USER
npx wrangler secret put FILE_PASS
```

Deploy:

```bash
npx wrangler deploy
```

## Endpoints

- `POST /c` — beacon endpoint. Your site's JS posts a small JSON blob here (page
  visit with `msclkid`, or a form submission) and it gets recorded in KV. Only
  accepts requests whose `Origin` header matches `ALLOWED_ORIGIN`.
- `GET /map` — returns everything recorded in KV as JSON, for the Python engine to
  pull and turn into ServiceTitan-matched conversions. Requires
  `Authorization: Bearer <OCI_BEARER>`.
- `PUT /f/{oci-clickid.csv|oci-pii.csv}` and `GET /f/{oci-clickid.csv|oci-pii.csv}` —
  the two conversion files. The engine `PUT`s a fresh file after each run
  (`Authorization: Bearer <OCI_BEARER>`); Microsoft Ads' scheduled import `GET`s it.

`FILE_USER` and `FILE_PASS` are the username and password you type into Microsoft
Ads' scheduled offline-conversion import form when you set the import URL to
`https://oci.yourdomain.com/f/oci-clickid.csv` (Basic auth) — that's the only place
those two secrets get used.
