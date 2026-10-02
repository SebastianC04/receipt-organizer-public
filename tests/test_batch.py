"""
Batch-level tests for process_image_batch_queue() with the model faked out --
no Ollama, no real receipts. Runs in a scratch directory (see _env.py), so
the real database and scan folders are never touched.

    python -m unittest discover tests
"""
import os
import unittest

import requests
from PIL import Image

from _env import import_extractor

extractor, WORKDIR = import_extractor()


def prepare_workdir():
    # extractor makes its folders and tables in the cwd only on first import, and
    # other test modules may have moved the cwd to their own scratch dir since --
    # so set up whichever scratch dir the test actually runs in.
    for folder in (extractor.PENDING_DIR, extractor.ARCHIVE_DIR, extractor.DUPLICATE_DIR, extractor.NEEDS_REVIEW_DIR):
        os.makedirs(folder, exist_ok=True)
    extractor.init_db()


def queue_receipt(name):
    Image.new("RGB", (200, 300), "white").save(os.path.join(extractor.PENDING_DIR, name))


class FailedReceiptsLeaveTheQueue(unittest.TestCase):
    """Every failure must move the file out of pending_scans, or a batch never finishes."""

    def setUp(self):
        prepare_workdir()
        self.original_post = extractor.requests.post

    def tearDown(self):
        extractor.requests.post = self.original_post

    def run_batch_with(self, fake_post, name):
        queue_receipt(name)
        extractor.requests.post = fake_post
        extractor.process_image_batch_queue("ria")
        return (name in os.listdir(extractor.PENDING_DIR), name in os.listdir(extractor.NEEDS_REVIEW_DIR))

    def test_model_timeout_moves_receipt_to_needs_review(self):
        def timeout(*a, **k):
            raise requests.exceptions.Timeout("model took too long")
        still_pending, in_review = self.run_batch_with(timeout, "timeout_receipt.jpg")
        self.assertFalse(still_pending, "a timed-out receipt must not stay in pending_scans")
        self.assertTrue(in_review)

    def test_model_unreachable_moves_receipt_to_needs_review(self):
        def unreachable(*a, **k):
            raise requests.exceptions.ConnectionError("ollama not running")
        still_pending, in_review = self.run_batch_with(unreachable, "unreachable_receipt.jpg")
        self.assertFalse(still_pending)
        self.assertTrue(in_review)


class ReviewReasons(unittest.TestCase):
    """Every receipt sent to needs_review gets a reason the dashboard can show."""

    def setUp(self):
        prepare_workdir()

    def test_send_to_review_moves_the_file_and_records_why(self):
        queue_receipt("odd_receipt.jpg")
        extractor.send_to_review(os.path.join(extractor.PENDING_DIR, "odd_receipt.jpg"), "odd_receipt.jpg",
                                 "Amount 700.0 doesn't match the receipt Total 714.0", "ria")
        self.assertIn("odd_receipt.jpg", os.listdir(extractor.NEEDS_REVIEW_DIR))
        entry = extractor.load_review_log()["odd_receipt.jpg"]
        self.assertEqual(entry["reason"], "Amount 700.0 doesn't match the receipt Total 714.0")
        self.assertEqual(entry["receipt_type"], "ria")

    def test_unreadable_log_counts_as_empty(self):
        with open(extractor.REVIEW_LOG, "w") as f:
            f.write("not json")
        self.assertEqual(extractor.load_review_log(), {})


if __name__ == "__main__":
    unittest.main()
