"""
Runs a small real batch through process_image_batch_queue() and checks where
each file ends up. Everything happens in a scratch directory with a fresh
database, so your real receipts.db and scan folders are never touched.

    python tests/live_batch.py --samples path/to/receipts --manifest tests/live_batch_manifest.example.json

The manifest says, per receipt type, which file should end up where:
"stored" (archived and logged), "needs_review", or "duplicate". Build one from
receipts you have: include a few normal ones, a void and a refund if you have
them, a bill or recharge, and any receipt whose fields the model tends to get wrong.

Needs a running Ollama with the model pulled.
"""
import argparse
import json
import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

from _env import REPO, use_utf8_output

DESTINATIONS = {"stored": "processed_archive", "needs_review": "needs_review", "duplicate": "duplicate_scans"}


def run_batch(workdir, samples, receipt_type, names):
    pending = workdir / "pending_scans"
    shutil.rmtree(pending, ignore_errors=True)
    pending.mkdir(parents=True)
    for name in names:
        shutil.copy(samples / name, pending)
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONPATH": str(REPO)}
    code = f"import extractor; extractor.process_image_batch_queue({receipt_type!r})"
    result = subprocess.run([sys.executable, "-c", code], cwd=workdir, env=env,
                            capture_output=True, text=True, encoding="utf-8", errors="replace")
    print(f"===== {receipt_type} batch =====")
    print("\n".join(line for line in result.stdout.splitlines() if "fitz" not in line))
    if result.returncode:
        print("STDERR:", result.stderr[-800:])


def main():
    use_utf8_output()
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--samples", required=True, help="folder holding the receipt files named in the manifest")
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--keep", action="store_true", help="keep the scratch directory and print its path")
    args = parser.parse_args()

    samples = Path(args.samples).resolve()
    manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    manifest = {k: v for k, v in manifest.items() if not k.startswith("_")}
    workdir = Path(os.environ.get("TEMP", "/tmp")) / f"receipt_live_batch_{os.getpid()}"
    shutil.rmtree(workdir, ignore_errors=True)
    workdir.mkdir(parents=True)

    for receipt_type, files in manifest.items():
        run_batch(workdir, samples, receipt_type, list(files))

    print("\n===== where each file ended up =====")
    mismatches = 0
    for receipt_type, files in manifest.items():
        for name, want in files.items():
            found = [label for label, folder in DESTINATIONS.items() if (workdir / folder / name).exists()]
            got = found[0] if found else ("still pending" if (workdir / "pending_scans" / name).exists() else "missing")
            ok = got == want
            mismatches += not ok
            print(f"{'OK  ' if ok else 'DIFF'} [{receipt_type}] {name}: expected {want}, got {got}")

    print("\n===== stored rows (check these by eye, especially canceled ones) =====")
    db = workdir / "receipts.db"
    if db.exists():
        connection = sqlite3.connect(db)
        for row in connection.execute("select image_filename, sender_name, recipient_name, amount, sequence_number, "
                                      "is_canceled, cancellation_type from deposits order by image_filename"):
            print(row)
        connection.close()

    print(f"\n{mismatches} mismatch(es).")
    if args.keep:
        print(f"Scratch directory kept: {workdir}")
    else:
        shutil.rmtree(workdir, ignore_errors=True)
    sys.exit(1 if mismatches else 0)


if __name__ == "__main__":
    main()
