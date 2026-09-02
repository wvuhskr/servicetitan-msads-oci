# Capture flow

This doc will explain the capture flow end to end: the on-site beacon fires on page load and on form submit, posts to the Cloudflare Worker's `/c` endpoint, and the Worker writes bindings into KV (Cloudflare's key-value store) for later matching against booked jobs. (Full writeup lands in Task 14.)

For now, see `worker/beacon.js` for the reference snippet to paste into a site or tag manager — set `WORKER` in that file to your deployed Worker URL.
