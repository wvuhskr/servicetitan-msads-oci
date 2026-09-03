# Click-date credit

Microsoft credits an offline conversion to the date of the original ad **click**, not the date you uploaded the conversion record, and not the date the ServiceTitan job was completed. This is expected, documented Microsoft behavior — not a bug in this tool — and the same is true on Google. It's worth knowing up front because it makes a working upload look, at a glance, like nothing happened.

## Why this trips people up

A ServiceTitan job commonly completes weeks after the ad click that produced it — a lead books today, the install happens in three weeks, the invoice closes a few days after that. When this tool uploads that completed job, Microsoft doesn't file the conversion under today's date. It looks up the click's own date (from the `msclkid` you sent) and backdates the conversion there.

So: you run `st-msads-oci build` today, `output/summary-latest.json` shows real rows uploaded, and the run notified you it worked — but if you then open Microsoft Ads and check "today" or "last 7 days," the conversion count doesn't move. That's not a failure. The conversion is sitting under a date weeks back that your report window doesn't cover.

## How to verify correctly

- Check that the **upload itself** worked using this tool's own output — `output/summary-latest.json` (or its run notification) for the day you ran it — not a Microsoft Ads report. Tier A / tier B counts and `published: true` tell you the file reached the Worker and Microsoft will pull it on schedule.
- Check that Microsoft **accepted and matched** what it pulled using `match_rate_history.csv`, once Microsoft's own result file has come back (see the README's [Reading results](../README.md#reading-results) and [`docs/setup.md`](setup.md) step 8).
- When you do look at conversion counts inside Microsoft Ads itself, use a report date range wide enough to cover the click date, not the upload date — as wide as your goal's conversion window (up to 90 days; see [`docs/setup.md`](setup.md)). "Last 7 days" or "today" will routinely read zero even on a healthy setup.
