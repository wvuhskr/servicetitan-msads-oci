# Input payload format

The engine (`st-msads-oci build`) consumes one JSON file. This doc mirrors the
machine schema at `src/st_msads_oci/input.schema.json` (JSON Schema Draft
2020-12) so you can write your own pull in any language, or use the bundled
ServiceTitan pull (`pull/pull_servicetitan.py`, invoked as `st-msads-oci pull`).

## The two rules

1. **Include every row, regardless of campaign.** The engine does the campaign
   filtering (matching against the Microsoft Ads goal categories). Your pull
   must not pre-filter rows by campaign. The only thing that's campaign-gated
   is *contact enrichment* — see "enrichment" below.
2. **A missing contact is `null` / `[]`, never invented.** If you don't have a
   customer's email, write `null` — not `""` (empty string fails schema
   validation). If you don't have any phone numbers, write `[]`, not a fake
   number.

## Timestamps

All timestamp fields (`generated_at`, `createdOn`, `completedOn`,
`receivedOn`) are ISO-8601 strings with an explicit offset or a trailing `Z`,
e.g. `2026-06-01T14:30:00Z` or `2026-06-01T10:30:00-04:00`. Treat all times as
UTC once parsed — the engine does not do timezone-aware business-hour logic.

## Top-level shape

```json
{
  "generated_at": "2026-06-01T00:00:00Z",
  "campaigns": [ ... ],
  "bookings": [ ... ],
  "jobs": [ ... ],
  "calls": [ ... ],
  "project_jobs": { ... }
}
```

`generated_at`, `campaigns`, `bookings`, `jobs`, `calls` are required.
`project_jobs` is optional (see below). No other top-level keys are allowed.

### `generated_at`

| field | type | nullable | required |
|---|---|---|---|
| `generated_at` | string (timestamp) | no | yes |

When the payload was built. The bundled pull sets it to the wall-clock time
of the pull run.

## `campaigns`

One row per ServiceTitan marketing campaign.

| field | type | nullable | required | meaning | bundled pull source |
|---|---|---|---|---|---|
| `id` | integer | no | yes | ServiceTitan campaign id | `GET /marketing/v2/tenant/{tenant}/campaigns` -> `id` |
| `name` | string | no | yes | campaign name | -> `name` |
| `category` | string | yes | yes | campaign category name, used to decide which campaigns are "Paid Microsoft" | -> `category.name` |

The engine matches campaigns against a configured category (example config
value: `campaign_category: "Paid Microsoft"`) to know which bookings/jobs/
calls are Microsoft-attributed and should get contact enrichment. Every
campaign is still listed here, not just the matching ones.

## `bookings`

One row per ServiceTitan booking (created, not necessarily completed).

| field | type | nullable | required | meaning | bundled pull source |
|---|---|---|---|---|---|
| `id` | integer | no | yes | booking id | `GET /crm/v2/tenant/{tenant}/bookings` -> `id` |
| `createdOn` | string (timestamp) | no | yes | when the booking was created | -> `createdOn` |
| `campaignId` | integer | yes | yes | campaign the booking is attributed to | -> `campaignId` |
| `customerName` | string | yes | yes | contact name | -> `name`, or the customer's name if enriched |
| `email` | string | yes | yes | contact email | `null` unless campaign matches (enrichment) |
| `phones` | array of string | no (array itself required) | yes | contact phone numbers | `[]` unless campaign matches (enrichment) |

Enrichment: only bookings whose `campaignId` is in the configured category
get contact data filled in. Source depends on how the booking arrived — a web
form booking's summary text is parsed for an email/phone line; a
technician-created booking looks up the linked job's customer record
(`GET /crm/v2/tenant/{tenant}/customers/{id}` +
`/crm/v2/tenant/{tenant}/customers/{id}/contacts`). Bookings outside the
category are listed with `email: null, phones: []` — they're still counted,
just not enriched.

## `jobs`

One row per **completed** ServiceTitan job. The bundled pull skips any job
with no `completedOn` — an in-progress job isn't emitted yet.

| field | type | nullable | required | meaning | bundled pull source |
|---|---|---|---|---|---|
| `id` | integer | no | yes | job id | `GET /jpm/v2/tenant/{tenant}/jobs` -> `id` |
| `completedOn` | string (timestamp) | no | yes | completion time | -> `completedOn` |
| `campaignId` | integer | yes | yes | attributed campaign | -> `campaignId` |
| `customerName` | string | yes | yes | customer name | `null` unless enriched |
| `email` | string | yes | yes | customer email | `null` unless enriched |
| `phones` | array of string | no | yes | customer phone numbers | `[]` unless enriched |
| `invoiceTotal` | number | yes | yes | sum of invoice totals for this job | `GET /accounting/v2/tenant/{tenant}/invoices?jobId={id}`, summed; only computed for enriched (category-matching) jobs, otherwise `null`; the API returns `total` as a decimal string, converted to a number before summing |
| `leadCallId` | integer | yes | yes | the call that generated this job, if any | -> `leadCallId` |
| `bookingId` | integer | yes | yes | the booking this job came from, if any | -> `bookingId` |
| `projectId` | integer | yes | yes | the project this job belongs to, if any | -> `projectId` |

Enrichment (customer name/email/phones/invoiceTotal) is limited to jobs whose
`campaignId` matches the configured category, same as bookings.

## `calls`

One row per **booked** call only. The bundled pull filters out any call whose
`leadCall.callType` is not `"Booked"` before it ever reaches the payload —
non-booked calls are not emitted as rows at all (that's the one place calls
differ from bookings/jobs, since non-booked calls carry no conversion signal
the engine needs).

| field | type | nullable | required | meaning | bundled pull source |
|---|---|---|---|---|---|
| `id` | integer | no | yes | call id | `GET /telecom/v2/tenant/{tenant}/calls` -> `leadCall.id` |
| `receivedOn` | string (timestamp) | no | yes | when the call was received | -> `leadCall.receivedOn` |
| `to` | string | yes | yes | number called | -> `leadCall.to` |
| `from` | string | yes | yes | caller number | -> `leadCall.from` |
| `direction` | string | no | yes | call direction | -> `leadCall.direction` |
| `campaign.id` | integer | yes | yes (object required) | attributed campaign id | -> `leadCall.campaign.id` |
| `callType` | string | no | no (schema-optional, but pull always sets `"Booked"`) | call outcome | fixed `"Booked"` (non-Booked calls are dropped before emission) |
| `customerName` | string | yes | conditional | customer name | `null` unless enriched |
| `email` | string | yes | conditional | customer email | `null` unless enriched |
| `phones` | array of string | no | conditional | customer phone numbers | `[]` unless enriched |

**Conditional rule:** if `callType` is `"Booked"`, then `customerName`,
`email`, and `phones` are all required keys (they may still hold `null` /
`[]` values — "required" means the key must be present, not that it must be
non-empty). Since the bundled pull only emits Booked calls, every call row it
writes carries all three keys.

Enrichment: only calls whose `campaign.id` matches the configured category
get contact data. The pull first tries the call's own embedded customer
record; if that customer has no contacts listed, it falls back to a
customer/contacts lookup by id, same as bookings and jobs.

## `project_jobs` (optional, v2 extension point)

Not emitted by the bundled v1 ServiceTitan pull. Reserved for a future
"suspect recovery" feature that recovers unattributed jobs by walking their
parent project. If present, it's an object keyed by **project id as a
string**, where each value is a list of job objects:

```json
{
  "project_jobs": {
    "9001": [
      {
        "id": 555001,
        "jobStatus": "Completed",
        "total": 450.0,
        "completedOn": "2026-06-02T18:00:00Z",
        "createdOn": "2026-05-30T09:00:00Z",
        "campaignId": null,
        "leadCallId": null,
        "bookingId": null,
        "partnerLeadCallId": null,
        "createdFromEstimateId": null,
        "customerName": null,
        "email": null,
        "phones": []
      }
    ]
  }
}
```

| field | type | nullable | required | meaning |
|---|---|---|---|---|
| `id` | integer | no | yes | job id |
| `jobStatus` | string | no | yes | ServiceTitan job status string |
| `total` | number | yes | yes | job total |
| `completedOn` | string | yes | yes | completion time (plain string, not schema-validated as a timestamp pattern) |
| `createdOn` | string (timestamp) | no | yes | creation time |
| `campaignId` | integer | yes | yes | attributed campaign, if any |
| `leadCallId` | integer | yes | yes | linked lead call, if any |
| `bookingId` | integer | yes | yes | linked booking, if any |
| `partnerLeadCallId` | integer | yes | yes | linked partner lead call, if any |
| `createdFromEstimateId` | integer | yes | yes | estimate this job was created from, if any |
| `customerName` | string | yes | no | contact name, if known |
| `email` | string | yes | no | contact email, if known |
| `phones` | array of string | no | no | contact phone numbers, if known |

If you write your own pull and have no use for this feature, omit the
`project_jobs` key entirely — it's not required.

## Validating a payload

From the repo root, in the macOS Terminal app:

```bash
st-msads-oci validate-input path/to/payload.json
```

Prints `ok` on success, or the list of schema errors (missing keys, wrong
types, empty-string emails, etc.) on failure.

Running the bundled pull:

```bash
st-msads-oci pull --config accounts.yaml --out output/input-latest.json
```

is equivalent to running `python3 pull/pull_servicetitan.py --config
accounts.yaml` directly — both call the same `pull_all()` / `write_payload_atomic()`
functions in `src/st_msads_oci/pull/servicetitan.py`, and both validate the
payload against the schema before writing it to disk.
