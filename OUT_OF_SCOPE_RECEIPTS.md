# Out-of-scope receipt types

## Problem

`extractor.py` only knows how to extract two document types: normal Ria
money-transfer receipts and MaxiTransfers receipts (`RIA_EXTRACTION_PROMPT`
and `MAXI_EXTRACTION_PROMPT`, selected via the `receipt_type` batch
parameter). But the real scan corpus also contains other document types that
share the same Ria/Maxi branding but are structurally different -- not money
transfers at all:

- **Phone recharge / top-up receipts** -- "Recharge Information" section
  (Country, Operator, Phone No.), no Order No/Total/Recipient fields.
- **Bill payment receipts** -- e.g. Comcast Cable, PG&E, AT&T, California
  Water Service. Has "Biller Name", "Account #", "Transaction No.", but no
  Sender/Recipient money-transfer structure. Several bills can share one
  receipt.

Without detection, the Ria/Maxi prompt would still try to extract Order
No/Sender/Recipient/Total from these -- fields that don't exist on the
document -- and the model would likely hallucinate plausible-looking but
wrong values rather than failing cleanly.

## Status: implemented

A pre-classification step now runs before extraction in
`process_image_batch_queue()` (Stage 1). `classify_document()` sends the
prepared image with `CLASSIFY_PROMPT` (a neutral, short prompt asking for one
of `money_transfer`, `phone_recharge`, `bill_payment`, `other`). Anything
that isn't `money_transfer` is moved to `needs_review/` and no extraction
call is made. An unparseable classifier answer is treated as `other`, so it
fails safe.

Why a separate call rather than a check on the extraction result: the
extraction prompt itself primes the model to find transfer fields, so a
post-hoc check on its output (including `raw_transcription`) inherits the
same hallucination. A neutral classification prompt doesn't have that
problem.

Cost: one extra vision call per file, about 3.7s each with `qwen2.5vl:7b`
(roughly 6 minutes added per 100-file batch).

## Validation (2026-09-29)

- **Labeled real receipts** (247, from `receipt-evaluation`, Ria and Maxi):
  247/247 classified `money_transfer` -- no false positives.
- **Original 8 known out-of-scope files:** 8/8 classified correctly.
- **Full sweep of `processed_archive/` + `needs_review/`** (727 files):
  704 `money_transfer`, 19 `bill_payment`, 4 `phone_recharge`. The 12 bill
  payments not previously known were checked by eye on a contact sheet --
  all real bill payments, no false positives.

### Out-of-scope files found in `processed_archive/`

These were archived and logged before the classifier existed, so their
(likely hallucinated) rows may be in the `Deposit` table. They have **not**
been moved or deleted.

`bill_payment` (19), all in `processed_archive/`:
`09-01-2026-` + `11-47-51`, `11-48-01`, `12-02-50`, `12-03-37`,
`12-16-50_001`, `12-19-47`, `12-19-59`, `12-20-05`, `12-24-46`, `12-32-58`,
`12-33-01`, `12-33-07`, `12-33-12`, `12-35-00`, `12-40-12_001`, `12-42-15`,
`12-42-17`, `12-45-58`, `12-54-52` (`.pdf`).

`phone_recharge` (4), already in `needs_review/`:
`09-01-2026-12-13-55`, `12-18-11`, `12-35-06`, `12-49-05` (`.pdf`).

## Remaining / not done

- Clean-up of the 19 archived bill payments (and any `Deposit` rows created
  from them) is not done; decide whether to delete the rows and move the
  files to `needs_review/`.
- The classifier is only validated on Ria and Maxi transfers vs. the two
  known out-of-scope types. A new document type would likely land in
  `other` (-> `needs_review/`), but that's untested.
- Ria vs. Maxi is still chosen per batch via `receipt_type`; only
  non-transfer documents are auto-detected.
