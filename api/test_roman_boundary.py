"""Verify roman names across card sides without requiring a correct email OCR."""
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import requests

from src.services.extractor import extract_card_fields
from src.services.kana_reading import apply_specialist_reading


def prediction(full, families):
    return {"model": "kanjikana-1.9o", "candidates": [{"reading": value} for value in full],
            "family_candidates": [{"reading": value} for value in families]}


class RomanBoundaryTests(unittest.TestCase):
    def setUp(self):
        config = patch("src.services.kana_reading.settings", SimpleNamespace(kana_base_url="http://kana:8001"))
        config.start()
        self.addCleanup(config.stop)

    def predict(self, name, family):
        if family == "月丘":
            return prediction(["ツキオカケイスケ", "ツキオガケイホ"], ["ツキオガ", "ツキオカ"])
        return prediction(["チガウヨミ"], ["チガウ"])

    def test_fragmented_front_fragments_and_back_roman_restore_both_fields(self):
        blocks = [{"text": text, "box": box, "_side": side} for text, box, side in (
            ("42 サンプル電機株式会社", [83, 40, 650, 102], "front"),
            ("品質保証部", [295, 154, 449, 181], "front"),
            ("主任 月 丘", [127, 201, 479, 253], "front"),
            ("景", [473, 202, 616, 256], "front"), ("輔", [706, 209, 750, 252], "front"),
            ("KEISUKE TSUKIOKA", [194, 184, 634, 211], "back"),
            ("CHIEF OF", [366, 224, 459, 239], "back"),
        )]
        source = "\n".join(block["text"] for block in blocks) + "\nE-mail:xsukioka-i@example.test"
        raw = {"person_name": "サンプル 電機株式会社", "company_name": "サンプル 電機株式会社",
               "person_name_kana": "サンプル デンキ 株式会社", "title": "主任 月 丘",
               "department": "品質保証部", "email": "xsukioka-i@example.test"}
        for previous in (None, {"person_name": "月丘景輔", "person_name_kana": "", "email": raw["email"]},
                         {"person_name": "月丘 景輔", "person_name_kana": "つきおが けいほ", "email": raw["email"]}):
            with self.subTest(previous=previous), \
                 patch("src.services.extractor._generate_structured_response", return_value=json.dumps(raw)), \
                 patch("src.services.kana_reading._predict", side_effect=self.predict):
                result = extract_card_fields(source, blocks, previous=previous).data
            self.assertEqual(result["person_name"], "月丘 景輔")
            self.assertEqual(result["person_name_kana"], "つきおか けいすけ")
            self.assertEqual(result["title"], "主任")
            self.assertEqual(result["company_name"], "サンプル電機 株式会社")
            self.assertEqual(result["_model"]["identity"]["selected"], "月丘 景輔")
            self.assertEqual(result["_model"]["kana"]["source"], "roman")
            self.assertEqual(result["_model"]["kana"]["verification"], "family_and_full_name_model_match")
            self.assertEqual(result["_raw"], raw)

    def test_surname_lengths_and_roman_order_are_verified_instead_of_assumed(self):
        for spelling, roman, reading in (("秦 嘉一郎", "KAICHIROU HATA", "はた かいちろう"),
                                          ("長谷川 亮", "HASEGAWA RYOU", "はせがわ りょう")):
            family, given = reading.split()
            def predict(name, surname):
                return prediction([family + given], [family]) if surname == spelling.split()[0] else prediction([], [])
            data = {"person_name": spelling.replace(" ", ""), "person_name_kana": ""}
            source = f"{data['person_name']}\n{roman}"
            with self.subTest(spelling=spelling), patch("src.services.kana_reading._predict", side_effect=predict):
                info = apply_specialist_reading(data, source, [])
            self.assertEqual(data["person_name"], spelling)
            self.assertEqual(data["person_name_kana"], reading)
            self.assertEqual(info["segmentation"], "verified")

    def test_a_family_match_alone_does_not_validate_a_different_roman_given_name(self):
        data = {"person_name": "月丘景輔", "person_name_kana": ""}
        with patch("src.services.kana_reading._predict", side_effect=self.predict):
            apply_specialist_reading(data, "月丘景輔\nTARO TSUKIOKA", [])
        self.assertEqual(data, {"person_name": "月丘景輔", "person_name_kana": ""})

    def test_ambiguous_roman_readings_or_boundaries_are_not_selected_by_order(self):
        for source, predict in (
            ("月丘景輔\nKEISUKE TSUKIOKA\nKEIHO TSUKIOGA", self.predict),
            ("月丘景輔\nKEISUKE TSUKIOKA", lambda name, family: self.predict(name, "月丘")),
        ):
            data = {"person_name": "月丘景輔", "person_name_kana": ""}
            with self.subTest(source=source), patch("src.services.kana_reading._predict", side_effect=predict):
                info = apply_specialist_reading(data, source, [])
            self.assertEqual(data["person_name"], "月丘景輔")
            self.assertEqual(data["person_name_kana"], "")
            self.assertEqual(info["status"], "uncertain")

    def test_roman_text_cannot_create_an_unprinted_kanji_identity(self):
        data = {"person_name": "月丘景輔", "person_name_kana": ""}
        with patch("src.services.kana_reading._predict") as predict:
            apply_specialist_reading(data, "サンプル電機株式会社\nKEISUKE TSUKIOKA", [])
        predict.assert_not_called()
        self.assertEqual(data["person_name_kana"], "")

    def test_model_outage_does_not_invent_a_boundary_from_four_kanji(self):
        data = {"person_name": "月丘景輔", "person_name_kana": ""}
        with patch("src.services.kana_reading._predict", side_effect=requests.ConnectionError()):
            apply_specialist_reading(data, "月丘景輔\nKEISUKE TSUKIOKA", [])
        self.assertEqual(data, {"person_name": "月丘景輔", "person_name_kana": ""})

    def test_saved_correction_does_not_override_a_model_verified_printed_reading(self):
        raw = {"person_name": "月丘 景輔", "person_name_kana": "つきおが けいほ"}
        correction = {"id": "old-reading", "context": raw, "corrected_value": "つきおが けいほ",
                      "automatic_value": "つきおか けいすけ", "reading_rules": [], "person_example": True}
        with patch("src.services.extractor._generate_structured_response", return_value=json.dumps(raw)), \
             patch("src.services.extractor.relevant_corrections", return_value=[correction]), \
             patch("src.services.kana_reading._predict", side_effect=self.predict):
            result = extract_card_fields("月丘 景輔\nKEISUKE TSUKIOKA", []).data
        self.assertEqual(result["person_name_kana"], "つきおか けいすけ")
        self.assertEqual(result["_feedback"]["applied_ids"], [])


if __name__ == "__main__":
    unittest.main()
