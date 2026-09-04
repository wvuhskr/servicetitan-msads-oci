# st-msads-oci-worker

Cloudflare Worker (a small script that runs on Cloudflare's edge network,
not on your own server) that captures Microsoft Ads click IDs (`msclkid`)
from your trusted booking server and serves the two CSV files Microsoft Ads' offline
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
  e.g. `https://www.yourdomain.com`) permitted when a capture request includes an Origin header. This is
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
npx wrangler secret put CAPTURE_BEARER
npx wrangler secret put OCI_BEARER
npx wrangler secret put FILE_USER
npx wrangler secret put FILE_PASS
```

Deploy:

```bash
npx wrangler deploy
```

## Endpoints

- `POST /c` receives verified server capture events with `Authorization: Bearer <CAPTURE_BEARER>`. The secret is separate from the export secret and must never be in browser code. See [the capture contract](../docs/capture.md).
- `GET /map` exports trusted capture events for the engine, with `Authorization: Bearer <OCI_BEARER>`. Responses use schema version 2 and a continuation cursor; the updated Python client consumes every page.
- `PUT /f/{oci-clickid.csv|oci-pii.csv}` and `GET /f/{oci-clickid.csv|oci-pii.csv}` —
  the two conversion files. The engine `PUT`s a fresh file after each run
  (`Authorization: Bearer <OCI_BEARER>`); Microsoft Ads' scheduled import `GET`s it.

`FILE_USER` and `FILE_PASS` are the username and password you type into Microsoft
Ads' scheduled offline-conversion import form when you set the import URL to
`https://oci.yourdomain.com/f/oci-clickid.csv` (Basic auth) — that's the only place
those two secrets get used.

For existing installations, follow [the security upgrade notes](../docs/security-upgrade.md) before deploying. The browser snippet alone no longer establishes capture bindings.
