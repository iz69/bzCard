"""Roman-labeled kanji recovery and logo-safe company extraction."""
import json
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np

from src.services.extractor import extract_card_fields
from src.services.address_evidence import prefer_complete_printed_address
from src.services.ocr import _refine_roman_labeled_names, _refine_truncated_prefectures


def initial_blocks():
    return [{"text": text, "box": box, "_side": "front"} for text, box in (
        ("88 有限会社 サンプル電機", [52, 47, 641, 107]),
        ("Yukino", [457, 158, 575, 175]), ("Ritsu", [625, 159, 718, 178]),
        ("野", [462, 186, 576, 225]), ("店", [670, 186, 713, 226]),
        ("E-Mail:yukino@example.test", [36, 395, 412, 426]),
    )]


class NameOcrTests(unittest.TestCase):
    def recognizer(self, contents=None, scores=None):
        return SimpleNamespace(recognizer=Mock(return_value=(SimpleNamespace(
            contents=contents or ["雪野", "雪野", "律", "律"], scores=scores or [.99] * 4), None)))

    def test_padded_recognition_requires_agreement_and_keeps_initial_evidence(self):
        blocks = initial_blocks()
        before = json.dumps(blocks)
        engine = self.recognizer()
        result = _refine_roman_labeled_names(engine, np.zeros((495, 840, 3)), blocks)
        self.assertEqual([result[index]["text"] for index in (3, 4)], ["雪野", "律"])
        self.assertEqual(result[3]["_recognition_retry"]["original_text"], "野")
        self.assertEqual(result[4]["_recognition_retry"]["original_text"], "店")
        self.assertEqual(result[3]["box"], blocks[3]["box"])
        self.assertEqual(json.dumps(blocks), before)
        engine.recognizer.assert_called_once()

    def test_synthetic_layout_recovers_name_and_roman_reading_after_ocr_retry(self):
        blocks = _refine_roman_labeled_names(self.recognizer(), np.zeros((495, 840, 3)), initial_blocks())
        source = "\n".join(block["text"] for block in blocks)
        raw = {"person_name": "霧村 湊", "person_name_kana": "きりむら みなと",
               "company_name": "有限会社 サンプル電機", "email": "yukino@example.test"}
        prediction = {"model": "kanjikana-1.9o", "candidates": [{"reading": "ユキノケン"}],
                      "family_candidates": [{"reading": "ユキノ"}]}
        with patch("src.services.extractor._generate_structured_response", return_value=json.dumps(raw)), \
             patch("src.services.kana_reading.settings", SimpleNamespace(kana_base_url="http://kana:8001")), \
             patch("src.services.kana_reading._predict", return_value=prediction):
            result = extract_card_fields(source, blocks).data
        self.assertEqual(result["person_name"], "雪野 律")
        self.assertEqual(result["person_name_kana"], "ゆきの りつ")
        self.assertEqual(result["company_name"], "有限会社 サンプル電機")
        self.assertEqual(result["_raw"], raw)
        self.assertEqual(result["_model"]["kana"]["source"], "roman")

    def test_complete_name_does_not_repeat_recognition(self):
        blocks = initial_blocks()
        blocks[3]["text"], blocks[4]["text"] = "雪野", "律"
        engine = self.recognizer()
        self.assertEqual(_refine_roman_labeled_names(engine, np.zeros((495, 840, 3)), blocks), blocks)
        engine.recognizer.assert_not_called()

    def test_disagreement_low_confidence_and_role_words_do_not_replace_ocr(self):
        cases = [(["雪野", "雪野", "律", "吾"], [.99] * 4),
                 (["雪野", "雪野", "律", "律"], [.99, .99, .89, .99]),
                 (["本社", "本社", "工場", "工場"], [.99] * 4)]
        for contents, scores in cases:
            blocks = initial_blocks()
            with self.subTest(contents=contents, scores=scores):
                result = _refine_roman_labeled_names(self.recognizer(contents, scores), np.zeros((495, 840, 3)), blocks)
            self.assertEqual(result, blocks)

    def test_recognition_failure_keeps_initial_ocr(self):
        blocks = initial_blocks()
        engine = SimpleNamespace(recognizer=Mock(side_effect=RuntimeError("recognition failed")))
        result = _refine_roman_labeled_names(engine, np.zeros((495, 840, 3)), blocks)
        self.assertEqual(result, blocks)

    def test_unrelated_emails_opposite_sides_and_distant_rows_are_not_joined(self):
        for mismatch in ("email", "side", "row", "column"):
            blocks = initial_blocks()
            if mismatch == "email":
                blocks[5]["text"] = "info@example.test"
            elif mismatch == "side":
                blocks[2]["_side"] = "back"
            elif mismatch == "row":
                for index in (2, 4):
                    blocks[index]["box"] = [value + (100 if axis in (1, 3) else 0)
                                             for axis, value in enumerate(blocks[index]["box"])]
            else:
                for index in (2, 4):
                    blocks[index]["box"] = [value + (500 if axis in (0, 2) else 0)
                                             for axis, value in enumerate(blocks[index]["box"])]
            engine = self.recognizer()
            with self.subTest(mismatch=mismatch):
                result = _refine_roman_labeled_names(engine, np.zeros((495, 1340, 3)), blocks)
                self.assertEqual(result, blocks)
                engine.recognizer.assert_not_called()

    def test_without_recovered_kanji_a_roman_reading_does_not_invent_a_name(self):
        blocks = initial_blocks()
        source = "\n".join(block["text"] for block in blocks)
        raw = '{"person_name":"霧村 湊","person_name_kana":"きりむら みなと"}'
        with patch("src.services.extractor._generate_structured_response", return_value=raw), \
             patch("src.services.kana_reading._predict") as predict:
            result = extract_card_fields(source, blocks).data
        self.assertEqual(result["person_name"], "")
        predict.assert_not_called()

    def test_grounded_company_is_not_overwritten_with_a_logo_prefix(self):
        for source, company, expected in (
            ("88 有限会社 サンプル電機", "有限会社 サンプル電機", "有限会社 サンプル電機"),
            ("77 株式会社ライトテック", "株式会社ライトテック", "株式会社 ライトテック"),
            ("株式会社88電機", "株式会社88電機", "株式会社 88電機"),
            ("88電機株式会社", "88電機株式会社", "88電機 株式会社"),
        ):
            with self.subTest(source=source), \
                 patch("src.services.extractor._generate_structured_response", return_value=json.dumps({"company_name": company})):
                result = extract_card_fields(source, []).data
            self.assertEqual(result["company_name"], expected)

    def test_spatial_roman_labels_are_scale_independent(self):
        for scale in (.5, 1, 3):
            blocks = initial_blocks()
            for block in blocks:
                block["box"] = [value * scale for value in block["box"]]
            engine = self.recognizer()
            with self.subTest(scale=scale):
                result = _refine_roman_labeled_names(engine, np.zeros((int(495 * scale), int(840 * scale), 3)), blocks)
            self.assertEqual([result[index]["text"] for index in (3, 4)], ["雪野", "律"])

    def test_truncated_company_still_recovers_the_full_printed_name(self):
        with patch("src.services.extractor._generate_structured_response",
                   return_value='{"company_name":"有限会社 サンプル"}'):
            result = extract_card_fields("有限会社 サンプル電機", []).data
        self.assertEqual(result["company_name"], "有限会社 サンプル電機")


class AddressOcrTests(unittest.TestCase):
    def test_extractor_only_restores_a_prefecture_character_from_complete_ocr(self):
        partial = "奈川県横浜市架空町2丁目4番68号"
        for source, expected in (("神" + partial, "神" + partial), (partial, partial),
                                 ("神奈川県横浜市架空町2丁目4番69号", partial)):
            with self.subTest(source=source):
                data = {"address": partial}
                prefer_complete_printed_address(data, source)
                self.assertEqual(data["address"], expected)

    def run_retry(self, original, readings, scores=None):
        blocks = [{"text": original, "box": [483, 354, 791, 375], "font_size": 21}]
        engine = SimpleNamespace(recognizer=Mock(return_value=(SimpleNamespace(
            contents=readings, scores=scores or [.99] * len(readings)), None)))
        return blocks, engine, _refine_truncated_prefectures(engine, np.zeros((489, 867, 3)), blocks)

    def test_truncated_prefecture_is_recovered_from_two_image_readings(self):
        for complete in ("神奈川県横浜市架空町2丁目4番68号", "東京都港区芝1-2-3", "大阪府大阪市北区1-2"):
            with self.subTest(address=complete):
                before, engine, result = self.run_retry(complete[1:], [complete, complete])
                self.assertEqual(result[0]["text"], complete)
                self.assertEqual(result[0]["box"], before[0]["box"])
                self.assertEqual(result[0]["_recognition_retry"]["original_text"], complete[1:])
                self.assertEqual(before[0]["text"], complete[1:])
                engine.recognizer.assert_called_once()

    def test_geographic_suffix_alone_never_supplies_missing_characters(self):
        original = "奈川県横浜市架空町2丁目4番68号"
        complete = "神" + original
        for readings, scores in (([complete, original], [.99, .99]),
                                 ([complete, complete], [.89, .99]),
                                 ([complete.replace("68", "69")] * 2, [.99, .99]),
                                 ([complete], [.99])):
            with self.subTest(readings=readings):
                before, _engine, result = self.run_retry(original, readings, scores)
                self.assertEqual(result, before)

    def test_complete_prefectures_and_nonaddresses_do_not_trigger_retry(self):
        for text in ("神奈川県横浜市架空町2丁目4番68号", "奈良県奈良市1-2-3", "奈川県技術研究所", "月丘 景輔"):
            with self.subTest(text=text):
                before, engine, result = self.run_retry(text, [])
                self.assertEqual(result, before)
                engine.recognizer.assert_not_called()

    def test_recognition_outage_preserves_the_initial_address(self):
        blocks = [{"text": "奈川県横浜市架空町2丁目4番68号", "box": [483, 354, 791, 375]}]
        engine = SimpleNamespace(recognizer=Mock(side_effect=RuntimeError("recognition failed")))
        self.assertEqual(_refine_truncated_prefectures(engine, np.zeros((489, 867, 3)), blocks), blocks)


if __name__ == "__main__":
    unittest.main()
