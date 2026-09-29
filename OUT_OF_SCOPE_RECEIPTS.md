# Out-of-scope receipt types (needs handling)

## Problem

`extractor.py` only knows how to extract two document types: normal Ria
money-transfer receipts and MaxiTransfers receipts (`RIA_EXTRACTION_PROMPT`
and `MAXI_EXTRACTION_PROMPT`, selected via the `receipt_type` batch
parameter). But the real scan corpus in `processed_archive/` also contains
other document types that share the same Ria/Maxi branding but are
structurally different -- not money transfers at all:

- **Phone recharge / top-up receipts** -- "Recharge Information" section
  (Country, Operator, Phone No.), no Order No/Total/Recipient fields.
- **Bill payment receipts** -- e.g. Comcast Cable, PG&E. Has "Biller Name",
  "Account #", "Transaction No.", but no Sender/Recipient money-transfer
  structure.

Today, if one of these gets scanned into `pending_scans/` and run through
`process_image_batch_queue()`, the current Ria/Maxi prompt will still try
to extract Order No/Sender/Recipient/Total from it -- fields that don't
exist on the document -- and the model will likely hallucinate plausible-
looking but wrong values rather than failing cleanly. There's no detection
step that recognizes "this isn't a receipt type I know" and routes it to
`needs_review/` the way a genuine extraction/parse failure already does.

## Known examples already in this repo

Confirmed instances, found by hand-reviewing the real corpus for the
`receipt-evaluation` project's ground-truth labeling (a separate project
that evaluates this pipeline's accuracy):

- `needs_review/09-01-2026-12-13-55.pdf` -- phone recharge receipt
- `processed_archive/09-01-2026-11-47-51.pdf` -- Comcast Cable bill payment
- `processed_archive/09-01-2026-12-24-46.pdf` -- PG&E bill payment
- `processed_archive/09-01-2026-12-33-01.pdf` -- PG&E bill payment
- `processed_archive/09-01-2026-12-33-07.pdf` -- PG&E bill payment
- `processed_archive/09-01-2026-12-33-12.pdf` -- PG&E bill payment
- `processed_archive/09-01-2026-12-35-00.pdf` -- PG&E bill payment
- `processed_archive/09-01-2026-12-42-17.pdf` -- PG&E bill payment

That's at least 8 known instances found in a partial review of ~250 of the
723 files in `processed_archive/` -- there are almost certainly more
unreviewed. All render fine with the existing `prepare_image_payload()` /
PyMuPDF path; the problem is purely that nothing recognizes them as the
wrong document type before attempting Ria/Maxi extraction.

## What already exists to build on

- `needs_review/` and the `shutil.move(...)` pattern in
  `process_image_batch_queue()` (Stage 1/2 failure handling) is the existing
  mechanism for "this receipt couldn't be processed automatically, a human
  needs to look at it." Routing unsupported document types there is
  consistent with that existing pattern, not a new concept.
- The batch queue currently takes a single `receipt_type` ("ria" or "maxi")
  for the *whole* batch and applies one prompt to every file
  (`get_extraction_prompt(receipt_type)`). Any fix should account for the
  fact that a batch is assumed to be homogeneous today.

## Not decided yet -- open design question

Roughly two directions, not evaluated against each other yet:

1. **Pre-classification step**: a cheap check (or a small classification
   prompt) before the main extraction call that asks "is this a Ria/Maxi
   money-transfer receipt, or something else?" and routes non-matches to
   `needs_review/` without spending a full extraction call on them.
2. **Post-hoc validation**: run extraction as today, but validate the
   result afterward (e.g. did we get *anything* plausible for Order
   No/Total/Recipient, or does the raw_transcription mention words like
   "Biller Name" / "Recharge" / "Account #" that don't belong) and route
   to `needs_review/` if it looks wrong rather than trusting a hallucinated
   result.

Worth deciding which approach (or both) before writing code.
