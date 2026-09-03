# How click capture works

Microsoft only gives you an ad click's ID (`msclkid`) at the moment someone clicks the ad and lands on your site — but the ServiceTitan booking or completed job that click produces might not exist for days. Capture is the piece that bridges that gap: it remembers the click, and matching (below) finds it again once a booking exists.

## Cookie -> beacon -> Worker

1. **The cookie.** When a visitor lands on any page with `?msclkid=...` in the URL, the beacon snippet ([`worker/beacon.js`](../worker/beacon.js)) writes it into a cookie for 90 days. Every later page view and form submission on that visit — or a return visit within 90 days — can read it back.
2. **The session beacon.** On every page view where the cookie is set, the beacon posts `{kind: "session", msclkid, dni_number}` to the Worker's `/c` endpoint. `dni_number` is only sent if you use dynamic number insertion (DNI — a script that swaps in a unique tracked phone number per visitor) and set `window.stMsadsDniNumber` yourself.
3. **The form beacon.** On every form submit anywhere on the page, the beacon posts `{kind: "form", msclkid, email, phone}` — whatever it can find in the submitted fields.
4. **The Worker writes bindings into KV** (Cloudflare's key-value store) so a later ServiceTitan pull can look them up. Nothing is sent to ServiceTitan or Microsoft at this point — this is capture only.

## What ends up in KV

| Key prefix | Written on | Contents | TTL |
|---|---|---|---|
| `id:e:<sha256>` | a form beacon with a parseable email | the click ID + timestamp | 100 days |
| `id:p:<sha256>` | a form beacon with a parseable phone | the click ID + timestamp | 100 days |
| `id:p10:<last 10 digits>` | a form beacon with any phone | the click ID + timestamp | 100 days |
| `dni:<last 10 digits>:...` | a session beacon carrying a DNI number | the click ID + timestamp | 40 days |
| `form:...` | every form beacon, with or without a click ID | the click ID (maybe null) + timestamp | 100 days |

Emails and phones are hashed (SHA-256) before they're ever written — the raw value never touches KV. `form:` records every submission regardless of whether a click ID was present, which is useful raw data for later analysis even for visits capture couldn't fully bind.

The click-ID cookie itself lives 90 days in the visitor's browser — longer than any KV TTL above — so cookie expiry is never what limits a match; the KV TTLs are.

## How a ServiceTitan row gets matched

For each ServiceTitan row (a booking, completed job, or booked call), the engine's `find_msclkid` tries the following, in order, stopping at the first hit:

1. **Email hash** — exact match against `id:e:`.
2. **Phone hash** — exact match against `id:p:`.
3. **Last-10-digit phone** — exact match against `id:p10:` (catches formatting differences a strict hash wouldn't).

If none of those hit, the fail-closed rule below decides whether the row is even allowed to try the next two steps:

4. **DNI pool** (only for rows with a linked ServiceTitan call — always true for booked calls, sometimes true for a completed job with a recorded lead call): look up the call's `to` number among `dni:` bindings, keep only those within **2 hours** before the call, and match only if exactly one click ID remains. Zero is no match; more than one is `pool_ambiguous`.
5. **Form proximity** (only for web bookings): look for a form submission carrying a click ID within **120 seconds** of the booking's `createdOn` timestamp. Exactly one candidate wins; more than one is `proximity_ambiguous`.

A row that matches at any step gets tier A (a real click ID, uploaded to `oci-clickid.csv`). A row that doesn't match, but still carries an email or phone hash, gets tier B (hashed PII only, uploaded to `oci-pii.csv` so Microsoft can still try its own identity matching). A row with neither is dropped outright.

## Fail closed on ambiguity

Steps 4 and 5 are a last resort, and the engine only lets a row reach them when guessing is actually safe. If a row carries an email or phone hash but steps 1-3 didn't find it, the engine assumes this contact was never captured on the site under a matchable identity, and refuses to guess via the DNI pool or form timing — it withholds the row as `no_session_captured` instead of risking a wrong match.

Booked calls are the one exception: a phone call has no on-site session to match by email or phone hash by definition, so booked-call rows always reach step 4 even if ServiceTitan happened to have an email or phone on file for that customer. Steps 1-3 still run first for every row, including booked calls — the exception only matters once those have all missed.

Either kind of ambiguity inside steps 4 or 5 (more than one click ID in the DNI pool, or more than one in the form-proximity window) also withholds the row rather than picking one at random.
