# Trusted click capture

The Worker now accepts capture events only from a trusted server using a separate `CAPTURE_BEARER` secret. The website-origin check is an additional restriction, not proof that a request or customer is genuine. Never put this secret in a web page, tag manager, mobile app, or browser request.

The browser reference in `worker/beacon.js` only remembers the landing-page click ID in the `st_msads_oci_msclkid` cookie. This cookie is an untrusted hint. The snippet alone no longer establishes customer bindings.

## Required booking-server integration

Your booking system must associate the contact and click with a verified submission or authenticated customer session before sending an event. Merely accepting an email typed into a public form does not verify that person's identity. Use the contact from your trusted customer/booking record and a click from the associated server-validated session. Do not build a public forwarding endpoint that attaches the capture secret to arbitrary browser input.

This repository provides the receiving Worker and the browser cookie helper. It cannot supply the customer verification step for an unspecified booking platform. Until that integration exists, leave capture disabled; the engine can still use the hashed-contact fallback for eligible conversions.

From your trusted booking server, send an HTTPS POST to the Worker's `/c` endpoint with `Authorization: Bearer <CAPTURE_BEARER>` and a JSON body. This illustrative body uses synthetic data:

```json
{
  "kind": "form",
  "event_id": "booking-opaque-identifier",
  "ts": "2026-09-04T12:00:00Z",
  "msclkid": "example-click-id",
  "email": "customer@example.com",
  "phone": "+12025550123"
}
```

Use a stable, non-personal event identifier containing only letters, digits, underscores or hyphens, up to 128 characters. The `ts` field is required. Send the persisted event timestamp, in ISO date/time format with timezone, and reuse the entire payload on retries. The event must not be in the future or older than the capture retention period. A server request may omit Origin; if supplied, it must exactly match the configured allowlist.

Record genuine verified submissions without an ad click too: omit `msclkid` or set it to null. These events remain available for capture analysis. A session event uses `kind: "session"` with the verified session's `msclkid` and `dni_number`, the tracked phone number assigned to that session. Session events require the same server-held secret and a stable event identifier.

For live use, replace the example event time and identifier with the persisted verified event. An identity event must precede or equal the conversion timestamp to qualify for matching. Do not replace the event time with the later delivery time.

## Storage and exports

Each event is stored atomically under `trusted:v2:<content fingerprint>`. Competing events are separate records, so a later submission cannot erase an earlier click. Identical payload retries share a storage key; they may still consume a write operation. Form events expire after 100 days, session events after 40 days. These are implementation retention limits, not Microsoft attribution settings.

Email and normalized phone are stored as SHA-256 fingerprints. The last ten phone digits and the tracked session number are also stored for fallback matching; this is customer data, not an anonymous dataset. The original pull input file also contains customer data.

The authenticated `/map` response includes `schema_version: 2`, `ids`, `dni`, `forms`, and `next_cursor`. The server returns one storage page per request. The Python client follows every continuation cursor and rejects legacy exports, missing completion markers, and repeated cursors. Old anonymous `id:`, `dni:` and `form:` keys are excluded. Do not copy them into the trusted namespace.

## Matching

Identity candidates must fall within 90 days before the conversion. The engine tries email fingerprint, phone fingerprint, then the final ten phone digits. Multiple distinct eligible clicks for an identity are withheld as ambiguous; repeated evidence for the same click is acceptable. Older and future bindings do not qualify.

Existing tracked-call and submission-time fallbacks remain available under the engine's eligibility rules. They now receive only authenticated capture events. A matching failure can still use the hashed-contact import tier when valid contact data exists. See [normalization.md](normalization.md) for supported inputs.

## Abuse controls

Authentication happens before reading the body, calling the optional limiter, or writing storage. Empty anonymous forms cannot create records. Request bodies are limited while streaming, before the complete payload is buffered. The capture limit is 2,048 bytes and the file-upload limit is 2 MiB.

The optional per-IP limiter limits authenticated senders too. If one server sends all captures, its requests share that limit; configure it for the expected server traffic. Removing it does not remove authentication. Application-level submission verification and bounded retention remain required.

Follow [security-upgrade.md](security-upgrade.md) before deploying this change to an existing installation.
