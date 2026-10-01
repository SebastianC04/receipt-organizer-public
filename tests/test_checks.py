"""
Unit tests for the post-extraction checks in extractor.py. No model, no
receipts, no network -- everything here is synthetic.

    python -m unittest discover tests
"""
import unittest

from _env import import_extractor

extractor, _ = import_extractor()


def transcription(transfer, fees, taxes, total):
    # Modeled on the layout the model transcribes from a Ria order receipt.
    return (f"Order No. US0000000001 Seq No. 11111 Transfer Amount (Monto) {transfer} USD "
            f"Transfer Fees (Cargos) {fees} USD Transfer Taxes (Impuestos) {taxes} USD "
            f"Total (Total) {total} USD RECIPIENT / BENEFICIARIO JANE DOE")


class AmountCheck(unittest.TestCase):
    def test_correct_total_passes(self):
        self.assertIsNone(extractor.find_amount_problem(transcription("700.00", "14.00", "0.00", "714.00"), 714.0))

    def test_pre_fee_transfer_amount_is_flagged(self):
        # The known failure: the model returned the Transfer Amount instead of the Total.
        problem = extractor.find_amount_problem(transcription("700.00", "14.00", "0.00", "714.00"), 700.0)
        self.assertIn("714", problem)

    def test_transcription_that_does_not_add_up_is_flagged(self):
        self.assertIsNotNone(extractor.find_amount_problem(transcription("700.00", "14.00", "0.00", "999.00"), 999.0))

    def test_amount_with_thousands_separator(self):
        self.assertIsNone(extractor.find_amount_problem(transcription("1,000.00", "20.00", "0.00", "1,020.00"), 1020.0))

    def test_figure_order_within_label_does_not_matter(self):
        # "Transfer Amount 210.89 USD (Monto...)": number before the parenthetical.
        text = ("Transfer Amount 210.89 USD (Monto) Transfer Fees 10.00 USD (Cargos) "
                "Transfer Taxes 2.11 USD (Impuestos) Total 223.00 USD (Total)")
        self.assertIsNone(extractor.find_amount_problem(text, 223.0))
        self.assertIsNotNone(extractor.find_amount_problem(text, 210.89))

    def test_missing_figures_are_skipped_not_flagged(self):
        self.assertIsNone(extractor.find_amount_problem("", 100.0))
        self.assertIsNone(extractor.find_amount_problem(None, 100.0))
        self.assertIsNone(extractor.find_amount_problem("Refund Date 01/01/2026 Total Refunded 50.00 USD", 50.0))

    def test_rounding_tolerance(self):
        self.assertIsNone(extractor.find_amount_problem(transcription("100.00", "5.00", "0.01", "105.00"), 105.0))


class RecipientNameCheck(unittest.TestCase):
    def test_ordinary_names_pass(self):
        for name in ("Jane Doe", "Maria de los Angeles Perez Lopez", "JOHN Q PUBLIC SMITH", "Juan Salvador Perez"):
            self.assertIsNone(extractor.find_recipient_name_problem(name), name)

    def test_absorbed_country_is_flagged(self):
        for name in ("Jane Doe Mexico", "Jane Doe GT", "Jane Doe El Salvador", "Jane Doe Guatemala"):
            self.assertIsNotNone(extractor.find_recipient_name_problem(name), name)

    def test_address_markers_are_flagged(self):
        for name in ("Jane Doe Sin Nombre", "Jane Doe Calle Uno", "Jane Doe Colonia Centro", "Jane Doe c/ Mayor"):
            self.assertIsNotNone(extractor.find_recipient_name_problem(name), name)

    def test_seven_or_more_words_is_flagged(self):
        self.assertIsNotNone(extractor.find_recipient_name_problem("One Two Three Four Five Six Seven"))
        self.assertIsNone(extractor.find_recipient_name_problem("One Two Three Four Five Six"))

    def test_garbled_character_is_flagged(self):
        self.assertIsNotNone(extractor.find_recipient_name_problem("Jane Mu�oz"))

    def test_empty_or_missing_does_not_crash(self):
        self.assertIsNone(extractor.find_recipient_name_problem(""))
        self.assertIsNone(extractor.find_recipient_name_problem(None))

    def test_whitespace_is_normalised(self):
        self.assertIsNotNone(extractor.find_recipient_name_problem("Jane   Doe \n Mexico"))


class ClassifierParsing(unittest.TestCase):
    """classify_document() must fail safe: anything unexpected becomes 'other'."""

    def _classify(self, content):
        class Resp:
            def raise_for_status(self): pass
            def json(self): return {"message": {"content": content}}
        original = extractor.requests.post
        extractor.requests.post = lambda *a, **k: Resp()
        try:
            return extractor.classify_document("ignored")
        finally:
            extractor.requests.post = original

    def test_known_labels(self):
        for label in extractor.CLASSIFY_LABELS:
            self.assertEqual(self._classify('{"document_type": "%s"}' % label), label)

    def test_unparseable_or_unknown_becomes_other(self):
        for content in ("not json", "{}", '{"document_type": "grocery_list"}', '{"document_type": null}', ""):
            self.assertEqual(self._classify(content), "other", content)

    def test_case_and_whitespace_are_tolerated(self):
        self.assertEqual(self._classify('{"document_type": "  Money_Transfer "}'), "money_transfer")


class VoidCheckParsing(unittest.TestCase):
    """detect_void_mark() must fail safe: anything but a clear true/false counts as marked (-> review)."""

    def _ask(self, content):
        class Resp:
            def raise_for_status(self): pass
            def json(self): return {"message": {"content": content}}
        original = extractor.requests.post
        extractor.requests.post = lambda *a, **k: Resp()
        try:
            return extractor.detect_void_mark("ignored")
        finally:
            extractor.requests.post = original

    def test_clear_answers(self):
        self.assertTrue(self._ask('{"void_marked": true}'))
        self.assertFalse(self._ask('{"void_marked": false}'))

    def test_unparseable_or_ambiguous_counts_as_marked(self):
        for content in ("not json", "{}", '{"void_marked": "no"}', '{"void_marked": null}', ""):
            self.assertTrue(self._ask(content), content)


class NameSecondRead(unittest.TestCase):
    """find_name_disagreement(): only a different *reading* counts, and a failed check is skipped, not flagged."""

    def _check(self, content, sender="JUAN PEREZ LOPEZ", recipient="MARIA PEREZ LOPEZ"):
        class Resp:
            def raise_for_status(self): pass
            def json(self): return {"message": {"content": content}}
        original = extractor.requests.post
        extractor.requests.post = lambda *a, **k: Resp()
        try:
            return extractor.find_name_disagreement("ignored", sender, recipient)
        finally:
            extractor.requests.post = original

    def test_same_reading_passes(self):
        self.assertIsNone(self._check('{"sender_name": "JUAN PEREZ LOPEZ", "recipient_name": "MARIA PEREZ LOPEZ"}'))

    def test_formatting_differences_are_not_disagreements(self):
        # case, extra spaces, missing space, accent, punctuation
        self.assertIsNone(self._check('{"sender_name": "juan  perez-lopez", "recipient_name": "MARÍA PEREZLOPEZ"}'))

    def test_one_letter_misreading_is_flagged(self):
        problem = self._check('{"sender_name": "JUAN PERES LOPEZ", "recipient_name": "MARIA PEREZ LOPEZ"}')
        self.assertIn("sender_name", problem)

    def test_absorbed_extra_line_is_flagged(self):
        problem = self._check('{"sender_name": "JUAN PEREZ LOPEZ", "recipient_name": "MARIA PEREZ LOPEZ"}',
                              recipient="MARIA PEREZ LOPEZ CALLE OCHO")
        self.assertIn("recipient_name", problem)

    def test_failed_or_empty_second_read_is_skipped(self):
        for content in ("not json", "{}", "[]", '{"sender_name": "", "recipient_name": null}', ""):
            self.assertIsNone(self._check(content), content)


if __name__ == "__main__":
    unittest.main()
