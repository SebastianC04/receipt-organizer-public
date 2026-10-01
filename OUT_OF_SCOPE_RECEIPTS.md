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
  Sender names have no check at all; the full run (below) found 2 sender
  spelling slips that were stored silently.
- Ollama's model runner (`llama-server`) grew to ~12.7 GB over the 247-file
  run (system memory 24% -> 70% of 31 GB), well beyond the ~6 GB the model
  itself needs. It didn't slow anything down, but a much larger batch could
  run out of RAM. Cause unknown (real leak vs. cache growth); no mitigation
  tried yet.
- Stopping `ollama serve` does NOT stop its `llama-server.exe` runner, which
  keeps the model in memory. Orphaned runners from earlier runs pushed memory
  to 85% and slowed a run to ~90 s/file. Kill both process names.
- (Done 2026-10-01) VOIDs written by hand across a receipt could be invisible
  to extraction; a separate VOID check now catches them (see below).

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

## Full pipeline run (2026-09-29)

All 247 labeled real receipts (182 Ria, 65 Maxi) were run through
`process_image_batch_queue()` in a scratch directory with a fresh database,
with the classifier (512px) and both post-extraction checks live, and every
stored row compared against its label. 49 minutes, a steady ~11.9 s/file on a
clean Ollama.

| Outcome | Files |
|---|---|
| Stored, exactly matching the label | 225 (91%) |
| Stored, but wrong vs. the label (silent errors) | 10 (4%) |
| Sent to `needs_review/` by the post-extraction checks | 10 |
| Sent to `needs_review/` by a model JSON failure | 1 |
| Rejected by the classifier, duplicates, crashes | 0 |

- **The checks had no false alarms.** All 10 files they flagged were genuinely
  wrong extractions: 7 bad recipient names (3 absorbed "MEXICO", 1 absorbed
  "GT", 3 with 7-8 words) and 3 bad amounts. This matches the offline
  prediction exactly (7/16 names, 3/3 amounts).
- **The 10 silent errors are the types the checks can't detect:** 4 recipients
  that absorbed a street line, 4 recipient spelling slips, and 2 sender
  spelling slips (a surname missing one letter). One further stored row
  differed from its label only because the model kept an accented "Ñ" that
  the label had stripped; it is not counted as an error.
- **The JSON failure is a safe, reproducible failure:** one receipt makes the
  model emit an invalid `\uXXXX` escape at the same character every time, so
  it always lands in `needs_review/`. It predates the classifier and checks.
- **Cancellations:** no mismatches among the stored voids and refunds.
- **Not covered:** this is the labeled set only (receipts that were already
  reviewed as normal), so it says nothing about how many new out-of-scope or
  unusual documents exist in fresh scans.

## VOID check (added 2026-10-01)

A synthetic receipt with VOID written by hand across the page (from the
`receipt-evaluation` regression set) exposed a gap: run on its own,
extraction missed the VOID in 4 of 4 tries -- and its own
`raw_transcription` never mentioned VOID either, so no check on extraction
output could have caught it. A VOID stored as a normal receipt is a live
transaction that should have been canceled, the most expensive kind of
silent error.

`detect_void_mark()` asks the model one separate question -- has this
receipt been marked VOID, stamped, printed or by hand? -- on the 512px copy
the classifier already uses. It runs in Stage 2b, **after** extraction, and
only for Ria receipts extraction read as not canceled (Maxi cancellations
are separate documents, not VOID marks), so the validated classifier ->
extraction sequence is unchanged. If it sees a VOID that extraction didn't,
the receipt goes to `needs_review/` rather than being auto-corrected. An
unparseable answer also goes to review (fail safe).

Validated before shipping:

| Check | Result |
|---|---|
| Direct question vs. extraction on the hand-written VOID | 4/4 found vs. 0/4 |
| 22 mixed receipts (3 synthetic voids, the real void, a real refund, 17 normal), at 1024px and 512px | 22/22 both sizes |
| Every real Ria receipt in the labeled set (182: 1 void, 181 not) | 0 false alarms, 0 missed |
| End to end: extraction forced to miss the VOID, real batch run | routed to `needs_review/`; a clean control stored normally |
| Regression set, two passes | 0 silent errors; check never fired on the 13 non-void receipts |

Cost: one extra model call (~3-4 s) per Ria receipt that isn't already a
cancellation. The real labeled set holds only one void, so the evidence for
catching voids is mostly synthetic; the false-alarm evidence is real.
