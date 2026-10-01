# Receipt Organizer

[![CI](https://github.com/SebastianC04/receipt-organizer-public/actions/workflows/ci.yml/badge.svg)](https://github.com/SebastianC04/receipt-organizer-public/actions/workflows/ci.yml)

A local, AI-powered tool for turning scanned money-transfer receipts (Ria, MaxiTransfers)
into structured, searchable records — replacing a manual paper-ledger workflow. Runs
entirely on your machine: receipts are read by a locally-hosted vision-language model
(Ollama + Qwen2.5-VL), nothing is sent to a third-party API.

## What it does

- **Batch-processes scanned receipts** (JPG/PNG/PDF/HEIC) from a watch folder.
- **Extracts structured fields** — date, sequence/folio number, sender, recipient, amount —
  via a local vision model, using a step-by-step "transcribe first, then extract" prompt
  to reduce hallucinated fields.
- **Flags high-value activity** — senders whose monthly total exceeds a threshold are
  automatically flagged for review, with drill-down to the original scanned image.
- **Handles duplicates and cancellations** — duplicate sequence numbers are set aside
  instead of double-logged; MaxiTransfers cancellation receipts are matched back to the
  original transaction.
- **Dashboard + full database view** — searchable, sortable table with CSV export, plus
  a snapshot/restore system for point-in-time backups of the database.

## Setup

You'll need Python 3.10+ and a running [Ollama](https://ollama.com) instance with the
`qwen2.5vl:7b` model pulled (`ollama pull qwen2.5vl:7b`).

```
python -m venv venv
venv\Scripts\activate        (Windows)   OR   source venv/bin/activate   (Mac/Linux)
pip install -r requirements-lock.txt
```

`requirements-lock.txt` pins the exact versions the app and its tests were verified with
(Python 3.14). `requirements.txt` is the loose list -- install from it instead to pick up
newer versions, then regenerate the lock file if everything still works.

If you move or rename the project folder, recreate the venv: on Windows its launchers
(`uvicorn.exe`, `pip.exe`) keep the old path and fail with "Unable to create process".
`python -m uvicorn main:app --reload` works in the meantime.

## Try it without real receipts

`generate_ria_mocks.py` generates 15 synthetic Ria-style receipt images (fake sender/recipient
names, randomized amounts, ~20% intentionally over the flagging threshold) into `pending_scans/`,
so you can see the full pipeline — extraction, flagging, dashboard — without any real data:

```
python generate_ria_mocks.py
```

## Run it

```
uvicorn main:app --reload
```

Then open **http://127.0.0.1:8000**. Drop receipt images into `pending_scans/` (created
automatically) and trigger a batch from the dashboard, or hit `POST /api/process-batch`.

## Project structure

```
main.py                 - FastAPI app & all API endpoints
database.py              - SQLAlchemy models (Deposit) and engine setup
extractor.py              - Batch pipeline: image prep, Ollama calls, parsing, archiving
analytics.py               - High-value-client flagging queries
view_db.py                  - Quick CLI dump of the database for debugging
generate_ria_mocks.py        - Synthetic receipt generator for demo/testing
static/, templates/           - Dashboard frontend (vanilla JS + Jinja2)
tests/                        - Unit tests and live model checks (see Tests)
```

## Tests

The unit tests need no model and no receipts:

```
python -m unittest discover tests
```

The `live_*.py` scripts in `tests/` need a running Ollama and receipts of your own
(none are included). All of them run in a scratch directory, so your real database
and scan folders are never touched:

- `live_extraction_interference.py` - checks that running the classifier doesn't change
  what extraction returns (this once made a VOID receipt come back as a normal one).
  Give it a voided receipt if you have one.
- `live_classifier_accuracy.py` - classifier accuracy on a folder of transfer receipts
  and a folder of other documents (bills, recharges).
- `live_batch.py` - runs a small batch through the real pipeline and checks where each
  file ends up, using a manifest like `live_batch_manifest.example.json`.

## Stack

FastAPI · SQLAlchemy · SQLite · Ollama (Qwen2.5-VL) · PyMuPDF (PDF handling) · Pillow
(HEIC conversion) · vanilla JS/TypeScript frontend
