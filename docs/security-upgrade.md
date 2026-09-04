# Security correction deployment notes

These corrections require a coordinated Worker, Python engine, and booking-server update. They are not a drop-in replacement for browser-only capture. No live deployment is performed by editing these files.

## Before deployment

In your deployment system, pause the conversion builder and Microsoft's scheduled imports during the upgrade. Preserve copies of the current output files and ledger, the local record of already processed conversions. Keep customer-data backups outside any public file-serving directory.

Implement and test the verified booking-server integration described in [capture.md](capture.md). Create a new random `CAPTURE_BEARER` secret shared only by that server and the Worker. Use different secrets for capture, engine export/upload, and Microsoft file reading. Do not expose the capture secret in the browser snippet.

In the Cloudflare Worker settings, store `CAPTURE_BEARER` alongside the existing `OCI_BEARER`, `FILE_USER` and `FILE_PASS` secrets. Deploy the Worker and the matching Python version together. The new engine rejects older map formats rather than using unverified historical bindings.

In your website template or tag manager, replace the old browser snippet with the updated cookie-only helper. The helper requires the trusted booking-server integration to establish attribution. Remove any public relay that merely forwards browser contact details with the server secret attached.

## Existing attribution data

Old anonymous capture records remain stored until expiration but are never exported by the new Worker. Do not migrate them into the trusted namespace. Existing dated click-ID exports may already contain assignments derived from those records; ignoring old capture keys alone does not remove those rows from the rolling CSV files.

Before resuming imports, review those dated exports and move unverified click-ID files to a private archive outside the output directory used for cumulative rebuilding. Preserve the ledger and previous imports for reconciliation. Do not reset watermarks or automatically replay historical conversions: this correction cannot determine whether previously imported revenue was credited correctly. Previously delivered Microsoft conversions are not reversed by these changes.

Run a fresh build and confirm that its import files contain only the intended records before resuming the schedules. The new dated-file merge preserves earlier rows across sequential same-day runs. Keep one build process active per project directory; concurrent builds remain unsupported.

## Email and error reporting

Mail delivery now verifies the server certificate on every supported port. Port 465 uses encryption from connection start; other ports, including 25 and 587, must support a verified STARTTLS upgrade. A plaintext-only relay will fail instead of receiving credentials or reports.

Validation alerts report field paths and broken rules without customer values. Other errors report their type and HTTP status where available, without serializing arbitrary response bodies, URLs, or stack traces. Notification failures use the same privacy rule in summary output. Both build and pull honor the option to disable notifications.

## Verification before resuming

In the staging environment, verify a real trusted submission reaches the Worker, appears in the authenticated map, matches the intended test conversion, and survives a second same-day build. Verify missing or incorrect capture credentials cannot write. Check normal mail delivery with the configured provider as well as certificate rejection in automated tests.

Local automated tests establish code behavior. They do not prove a particular site's customer verification, Cloudflare deployment, mail-provider setup, or Microsoft import result.
