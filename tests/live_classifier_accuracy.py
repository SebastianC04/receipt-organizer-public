"""
Measures the document classifier on folders of your own receipts.

    python tests/live_classifier_accuracy.py --transfers path/to/normal_receipts --other path/to/bills_and_recharges

--transfers: money-transfer receipts (Ria/Maxi, including voids and refunds);
each should be classified `money_transfer`.
--other: anything that is NOT a money-transfer receipt; none should be.

A transfer sent to review costs manual work; an "other" document classified
as a transfer silently stores bad data, so the second kind of miss matters more.
Needs a running Ollama. Uses the same 512px image the pipeline gives the classifier.
"""
import argparse
import collections
import sys
import time
from pathlib import Path

from _env import import_extractor

EXTENSIONS = (".pdf", ".jpg", ".jpeg", ".png")


def files_in(folder):
    return sorted(p for p in Path(folder).iterdir() if p.suffix.lower() in EXTENSIONS)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--transfers", help="folder of money-transfer receipts")
    parser.add_argument("--other", help="folder of non-transfer documents")
    args = parser.parse_args()
    if not (args.transfers or args.other):
        parser.error("give --transfers and/or --other")

    x, _ = import_extractor()
    wrong = []
    for folder, expect_transfer in ((args.transfers, True), (args.other, False)):
        if not folder:
            continue
        labels = collections.Counter()
        started = time.time()
        paths = files_in(folder)
        for path in paths:
            label = x.classify_document(x.prepare_image_payload(str(path), x.CLASSIFY_IMAGE_PX))
            labels[label] += 1
            if (label == "money_transfer") != expect_transfer:
                wrong.append((path.name, label, expect_transfer))
                print(f"FAIL {path.name}: classified {label}", flush=True)
        kind = "transfers" if expect_transfer else "other"
        correct = len(paths) - sum(1 for w in wrong if w[2] == expect_transfer)
        print(f"{kind}: {correct}/{len(paths)} correct {dict(labels)}  "
              f"({(time.time() - started) / max(len(paths), 1):.1f}s/file)")
    missed_other = sum(1 for w in wrong if not w[2])
    print(f"\n{len(wrong)} wrong ({missed_other} non-transfer documents let through to extraction).")
    sys.exit(1 if wrong else 0)


if __name__ == "__main__":
    main()
