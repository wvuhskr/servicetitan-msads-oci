# Security Policy

## Reporting a vulnerability

Please report security vulnerabilities using GitHub's **private vulnerability
reporting**, not a public issue: go to this repo's **Security** tab and click
**"Report a vulnerability"**. This opens a private draft advisory that only the
maintainer can see until it's resolved.

Do not open a public issue for a security report.

## What NOT to include in a report (or any public issue)

Never paste any of the following, anywhere public:

- Credentials, API secrets, or bearer tokens.
- The contents of your `.env` (`ST_CLIENT_SECRET`, `ST_APP_KEY`, `OCI_WORKER_BEARER`,
  `GRAPH_CLIENT_SECRET`, `SMTP_PASSWORD`, and the rest) or your `accounts.yaml`.
- Worker secrets or `worker/wrangler.toml`: the `OCI_BEARER`, `FILE_USER`, and
  `FILE_PASS` values, your Cloudflare account ID, or your KV namespace ID.
- The engine's data files (`output/`, `results/`, `summary-latest.json`): these can
  contain hashed customer emails and phone numbers, ServiceTitan and Microsoft account
  IDs, campaign names, and other payload detail.

If you have already pasted one of these into a public issue, rotate the affected
credential immediately and ask a maintainer to delete or redact the comment.

## Supported versions

Only the latest released version is supported. This is a best-effort,
community-maintained project, so there is no formal SLA for fixes or patches.

## Response expectations

There is no guaranteed response time. Reports are triaged as time allows, with priority
given to anything that could lead to credential exposure, leakage of customer PII, or
unintended writes to a live Microsoft Ads account.
