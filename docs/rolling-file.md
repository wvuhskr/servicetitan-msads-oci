# The rolling file

The two files the Worker serves — `oci-clickid.csv` and `oci-pii.csv` — are not "today's new rows." Every `st-msads-oci build` run rebuilds them from scratch as the deduped union of every dated snapshot (`oci-clickid_YYYY-MM-DD.csv`, written once per day that produces new rows) from the last **14 days**, and republishes the result to the Worker — even on a day with zero new rows.

## The race it prevents

If the served file only ever held the rows from whichever run built it most recently, a row would only be available to Microsoft during the narrow window between when it was built and when the *next* run overwrote the file. Microsoft's own daily pull doesn't run in lockstep with this tool's schedule — if the pull happens to land outside that window even once (a delayed run, a skipped day, a machine asleep overnight), the row is gone before Microsoft ever fetches it, silently, with nothing anywhere flagging it as missed.

Rebuilding the served file as a rolling 14-day window every run closes that gap: a row stays in what's served across many runs, not just one, so it survives until Microsoft actually has a chance to pull it.

## Why re-serving the same row is fine

Microsoft's offline-conversion import dedupes what it's already ingested — importing the same click ID + conversion name + conversion time + hashed identity twice doesn't double-count it. So serving 14 days of history on every run, most of which Microsoft has already seen, costs nothing on Microsoft's side. That's what makes the rolling window safe to be generous with.

## The Worker's separate staleness guard

There's a second, independent safety net at the Worker itself: if the engine stops publishing entirely for more than 14 days (a crashed scheduled task, expired credentials, a dead machine), the Worker notices the file it's holding hasn't been refreshed in that long and serves an empty, header-only CSV instead of letting Microsoft keep re-importing a file that's gone stale. This guard and the engine's rolling window happen to share the same 14-day number by design, but they're checking different things — "how far back the engine looks when rebuilding the file" versus "how long the Worker keeps serving a file nobody's refreshed" — and would each still make sense set to a different number.

## What this means day to day

Missing a run now and then is fine — a booked job or completed job still reaches Microsoft as long as some run happens within 14 days of it being built. Missing more than 14 days in a row is the one case worth watching for: a row that got built into a dated snapshot but never actually served in that stretch ages out of the rolling window and won't come back on its own — the engine's ledger already marks it as uploaded the moment it's written into a dated file, so a later run won't rebuild it either. Keep an eye on `output/summary-latest.json` and your run notifications — that's what would surface a gap like this happening in the first place.

## Repeated runs on the same day

Sequential builds merge new conversion rows into the existing dated file and replace it atomically. They no longer overwrite earlier rows with only the newest batch. The rolling file includes the merged daily history, including when a later run adds rows in only one tier. Run only one build process at a time per project directory.
