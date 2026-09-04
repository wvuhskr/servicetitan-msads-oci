# Shared contact normalization

Python and JavaScript use the same explicit input policy so the same supported contact becomes the same SHA-256 fingerprint. Existing ordinary email, common Latin accents, dot removal, plus-address removal, and US phone formatting remain supported.

Email inputs must be strings. Both implementations trim the same explicit whitespace characters, remove ordinary embedded spaces, decompose supported Latin accents and full-width ASCII characters, remove fixed combining-mark ranges, and lowercase. The result must contain ASCII characters with one `@` and nonempty local and domain parts. Dots and the plus suffix are removed from the local part as in the existing feed contract.

Supported input ranges are ASCII U+0020–U+007E, Latin U+00C0–U+024F and U+1E00–U+1EFF, full-width ASCII U+FF01–U+FF5E, and combining ranges U+0300–U+036F, U+1AB0–U+1AFF, U+1DC0–U+1DFF, U+20D0–U+20FF and U+FE20–U+FE2F. A character that remains non-ASCII after decomposition is rejected. Other scripts, including Greek and Cyrillic email addresses, return no email identity. A valid phone may still supply the conversion's identity. This conservative policy avoids depending on different Unicode databases in different runtime versions.

The trimmed whitespace set is U+0009–U+000D, U+0020, U+0085, U+00A0, U+1680, U+2000–U+200A, U+2028, U+2029, U+202F, U+205F, U+3000 and U+FEFF.

Phone inputs must also be strings. Only ASCII digits `0` through `9` are retained. Ten digits become a US number beginning `+1`; eleven digits beginning `1` receive the leading `+`. Other lengths do not produce a normalized phone. Last-ten-digit matching uses the same ASCII digit extraction in both languages. Non-ASCII digits are never interpreted as ASCII digits.

The shared fixtures lock the ordinary outputs and the review's edge cases. A Python test also executes the JavaScript implementation and compares both normalized values and fingerprints across supported ranges and unsupported boundary characters. The test uses Node.js, the JavaScript runtime, in addition to Python. The repository's automated checks install both before testing.

Existing imports and ledger records are not rewritten. Changed edge-case identities and previously unsupported scripts can change matching outcomes; apply the capture migration described in [security-upgrade.md](security-upgrade.md) rather than relabeling old fingerprints as trusted.
