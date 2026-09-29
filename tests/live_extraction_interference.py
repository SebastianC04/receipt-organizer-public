"""
Checks that running the classifier does not change what extraction returns.

Background: sending Ollama the identical image for the classifier prompt and
the extraction prompt once made extraction return is_cancellation=false on a
VOID receipt, even though the model's own transcription contained "VOID". This
runs extraction on its own, then extraction right after the classifier call
(the order process_image_batch_queue() uses), and fails if any field differs.

Needs a running Ollama with the model pulled. Pass receipts you have; a
voided or refunded one is the most useful. Nothing is written to the real
database or scan folders.

    python tests/live_extraction_interference.py --type ria path/to/void.pdf path/to/normal.pdf
"""
import argparse
import json
import sys

import requests

from _env import import_extractor

FIELDS = ("is_cancellation", "cancellation_type", "amount", "sender_name", "recipient_name",
          "sequence_number", "order_number", "date")


def extract(x, image, receipt_type):
    payload = {"model": x.OLLAMA_MODEL, "format": "json", "stream": False, "options": {"temperature": 0.0},
               "messages": [{"role": "user", "content": x.get_extraction_prompt(receipt_type), "images": [image]}]}
    response = requests.post(x.OLLAMA_URL, json=payload, timeout=x.REQUEST_TIMEOUT)
    response.raise_for_status()
    return json.loads(response.json()["message"]["content"])


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("files", nargs="+")
    parser.add_argument("--type", choices=("ria", "maxi"), default="ria")
    parser.add_argument("--repeats", type=int, default=2, help="runs per condition (default 2)")
    args = parser.parse_args()

    x, _ = import_extractor()
    failures = 0
    for path in args.files:
        full = x.prepare_image_payload(path)
        # Mirror the pipeline exactly: the classifier gets its own smaller copy.
        small = x.prepare_image_payload(path, x.CLASSIFY_IMAGE_PX)
        alone = [extract(x, full, args.type) for _ in range(args.repeats)]
        after = []
        for _ in range(args.repeats):
            label = x.classify_document(small)
            after.append(extract(x, full, args.type))
        baseline = {f: alone[0].get(f) for f in FIELDS}
        bad = [(f, baseline[f], run.get(f)) for run in alone[1:] + after for f in FIELDS if run.get(f) != baseline[f]]
        status = "OK  " if not bad else "FAIL"
        failures += bool(bad)
        print(f"{status} {path}  (classifier said: {label})")
        for field, was, now in bad:
            print(f"       {field}: {was!r} alone -> {now!r} in another run")
    print("\nAll consistent." if not failures else f"\n{failures} file(s) changed between conditions.")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
