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

Cost: one extra vision call per file. It was about 3.7s each with
`qwen2.5vl:7b` at 1024px; it now runs on a 512px copy, which should be
faster but wasn't re-timed.

**The classifier must not see the same image as extraction.** Sending Ollama
the identical image for both prompts broke extraction: a VOID receipt came
back `is_cancellation: false` 3/3 times (0/3 when extraction ran alone), even
though its own transcription contained "VOID". The cause was inferred, not
proven (likely Ollama reusing cached state for the identical image); a 512px or
768px copy for the classifier fixed it 3/3. This slipped past the classifier
tests, which only checked labels, and was found by running a real batch
(`CLASSIFY_IMAGE_PX` in `extractor.py`). It was live in the first pushed
version (`304d329`) until the fix.

## Validation (2026-09-29)

Numbers in the first three bullets were measured at 1024px. Re-measured at
the current 512px: 247/247 labeled receipts and 23/23 out-of-scope files
(the 4 recharges + 19 bills) classified correctly. A 17-file real batch
(isolated scratch dir, mixed Ria/Maxi/void/refund/bill/recharge/bad-extraction
cases) landed every file where expected, and the void was stored as a void.

- **Labeled real receipts** (247, from `receipt-evaluation`, Ria and Maxi):
  247/247 classified `money_transfer` -- no false positives.
- **Original 8 known out-of-scope files:** 8/8 classified correctly.
- **Full sweep of `processed_archive/` + `needs_review/`** (727 files):
  704 `money_transfer`, 19 `bill_payment`, 4 `phone_recharge`. The 12 bill
  payments not previously known were checked by eye on a contact sheet --
  all real bill payments, no false positives.

### Out-of-scope files found in `processed_archive/`

These were archived and logged before the classifier existed, with
hallucinated `Deposit` rows. They have since been cleaned up (see below).

`bill_payment` (19), all in `processed_archive/`:
`09-01-2026-` + `11-47-51`, `11-48-01`, `12-02-50`, `12-03-37`,
`12-16-50_001`, `12-19-47`, `12-19-59`, `12-20-05`, `12-24-46`, `12-32-58`,
`12-33-01`, `12-33-07`, `12-33-12`, `12-35-00`, `12-40-12_001`, `12-42-15`,
`12-42-17`, `12-45-58`, `12-54-52` (`.pdf`).

`phone_recharge` (4), already in `needs_review/`:
`09-01-2026-12-13-55`, `12-18-11`, `12-35-06`, `12-49-05` (`.pdf`).

## Remaining / not done

- (Done 2026-09-29) The 19 archived bill payments were moved to
  `needs_review/` and their 19 `Deposit` rows deleted (backup:
  `snapshots/receipts.db.pre-billcleanup`).
- Ria vs. Maxi is still chosen per batch via `receipt_type`; only
  non-transfer documents are auto-detected.
- A money-transfer receipt from a *different* company (tested with a
  Western Union-style receipt) is classified `money_transfer` and goes
  through Ria/Maxi extraction, since the classifier asks "is this a transfer
  receipt", not "is this Ria/Maxi". Nothing catches this yet.
- The extraction checks below can't detect 9 of the 16 wrong recipient names
  in the labeled run (street lines with no marker word, and spelling slips).

## Unseen document types (tested 2026-09-29)

Synthetic documents the classifier had never seen, plus the labeled set's
cancellations:

| Document | Result |
|---|---|
| blank page, bank deposit slip, random noise | `other` |
| grocery receipt, plumbing invoice | `bill_payment` (wrong label, still routed to `needs_review/`) |
| Western Union-style transfer | `money_transfer` (see above) |
| void and refund Ria receipts in the labeled set (1 void, 2 refunds) | `money_transfer` (3/3) |

The cancellation sample is small, and the labeled set has no Maxi
cancellation. The synthetic documents are simple renderings, not real scans.

## Post-extraction checks (added 2026-09-29)

Two known extraction errors (documented in the `extractor.py` comments) are
now caught in code, in Stage 2b of `process_image_batch_queue()`, and the
file is moved to `needs_review/` instead of being stored:

- `find_amount_problem()` (Ria only): reconciles the Transfer Amount, Fees
  and Taxes the model wrote in `raw_transcription`, and compares the Total
  with the extracted `amount`. The bad amounts were the pre-fee Transfer
  Amount, not "Total to Recipient" as the old comment said.
- `find_recipient_name_problem()`: flags a recipient name ending in a country
  name, containing an address marker, 7+ words long, or containing a garbled
  character.

Checked against the 247-receipt labeled run (`receipt-evaluation`, commit
`90ecd73`):

| Check | Caught | False positives |
|---|---|---|
| amount | 3/3 | 0/243 |
| recipient name | 7/16 | 0/230 |

The amount check parses on 179 of 182 Ria receipts; the other 3 are skipped,
not flagged. This was measured on saved model output, not by re-running the
model, so the live pipeline hasn't been re-run with these checks.
