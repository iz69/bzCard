"""Printed roman names outrank kanji guesses, including bounded OCR repairs."""
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import requests

from src.services.kana_reading import apply_specialist_reading
from src.services.extractor import extract_card_fields


def prediction(readings, families=()):
    return {"model": "kanjikana-1.9o",
            "candidates": [{"reading": value} for value in readings],
            "family_candidates": [{"reading": value} for value in families]}


class RomanReadingTests(unittest.TestCase):
    def setUp(self):
        config = patch("src.services.kana_reading.settings", SimpleNamespace(kana_base_url="http://kana:8001"))
        config.start()
        self.addCleanup(config.stop)

    def predict(self, name, family):
        if name == "晴輔":
            return prediction(["セイスケ", "ハルスケ", "アスケ"])
        return prediction(["ウンバラセイスケ", "クモハラセイスケ"], ["ウンバラ", "クモバラ", "クモハラ"])

    def test_single_component_email_links_an_adjacent_roman_identity(self):
        for separator in (" ", "\n"):
            data = {"person_name": "雲原 晴輔", "person_name_kana": "うんばら せいすけ"}
            source = f"雲原 晴輔{separator}Harusuke Kumohara\nkumohara@example.test"
            with self.subTest(separator=separator), \
                 patch("src.services.kana_reading._predict", side_effect=self.predict):
                info = apply_specialist_reading(data, source, [])
            self.assertEqual(data["person_name_kana"], "くもはら はるすけ")
            self.assertEqual(info["source"], "roman")

    def test_unreadable_roman_token_matches_a_unique_component_model_candidate(self):
        for roman in ("Harusuce Kumohara", "Kumohara Harusuce"):
            data = {"person_name": "雲原 晴輔", "person_name_kana": "うんばら せいすけ",
                    "email": "kumohara@example.test"}
            previous = dict(data)
            source = f"雲原 晴輔 {roman}\nkumohara@example.test"
            with self.subTest(roman=roman), \
                 patch("src.services.kana_reading._predict", side_effect=self.predict) as predict:
                info = apply_specialist_reading(data, source, [], previous=previous)
            self.assertEqual(data["person_name_kana"], "くもはら はるすけ")
            self.assertEqual(info["source"], "roman")
            self.assertEqual(info["roman_ocr_corrections"][0]["observed"], "harusuce")
            self.assertIn("harusuke", info["roman_ocr_corrections"][0]["roman_candidates"])
            predict.assert_any_call("晴輔", "")

    def test_ocr_repair_is_not_specific_to_one_person_or_one_letter(self):
        data = {"person_name": "川辺 信輔", "person_name_kana": "かわべ のぶすけ"}
        source = "川辺 信輔 Shinsuce Kawabe\nkawabe@example.test"
        def predict(name, family):
            return prediction(["ノブスケ", "シンスケ"]) if name == "信輔" else prediction(["カワベノブスケ"], ["カワベ"])
        with patch("src.services.kana_reading._predict", side_effect=predict):
            info = apply_specialist_reading(data, source, [])
        self.assertEqual(data["person_name_kana"], "かわべ しんすけ")
        self.assertEqual(info["source"], "roman")

    def test_absent_or_ambiguous_ocr_repairs_do_not_leave_a_kanji_guess(self):
        source = "雲原 晴輔 Harusuce Kumohara\nkumohara@example.test"
        for readings in (["セイスケ"], ["ハルスケ", "ハルスセ"]):
            def predict(name, family):
                return prediction(readings) if name == "晴輔" else self.predict(name, family)
            data = {"person_name": "雲原 晴輔", "person_name_kana": "うんばら せいすけ"}
            with self.subTest(readings=readings), patch("src.services.kana_reading._predict", side_effect=predict):
                info = apply_specialist_reading(data, source, [])
            self.assertEqual(data["person_name_kana"], "")
            self.assertEqual(info["status"], "uncertain")

    def test_valid_roman_spelling_is_never_changed_to_a_model_candidate(self):
        data = {"person_name": "雲原 晴輔", "person_name_kana": "うんばら せいすけ"}
        source = "雲原 晴輔 Harusuku Kumohara\nkumohara@example.test"
        with patch("src.services.kana_reading._predict", side_effect=self.predict) as predict:
            info = apply_specialist_reading(data, source, [])
        self.assertEqual(data["person_name_kana"], "くもはら はるすく")
        self.assertEqual(info["source"], "roman")
        self.assertFalse(any(call.args[0] == "晴輔" for call in predict.call_args_list))

    def test_unrelated_roman_words_and_single_email_do_not_supply_a_reading(self):
        for source in ("雲原 晴輔\nBUREAU VERITAS\nkumohara@example.test",
                       "雲原 晴輔 Harusuce Kumohara\ninfo@example.test",
                       "Harusuce Kumohara\n雲原 晴輔\nkumohara@example.test"):
            data = {"person_name": "雲原 晴輔", "person_name_kana": "うんばら せいすけ"}
            with self.subTest(source=source), patch("src.services.kana_reading._predict", side_effect=self.predict) as predict:
                info = apply_specialist_reading(data, source, [])
            self.assertEqual(data["person_name_kana"], "うんばら せいすけ")
            self.assertEqual(info["source"], "specialist")
            predict.assert_called_once()

    def test_unreadable_printed_roman_name_is_not_ignored_when_models_are_unavailable(self):
        source = "雲原 晴輔 Harusuce Kumohara\nkumohara@example.test"
        for configured in (True, False):
            data = {"person_name": "雲原 晴輔", "person_name_kana": "うんばら せいすけ"}
            with self.subTest(configured=configured), \
                 patch("src.services.kana_reading.settings", SimpleNamespace(kana_base_url="http://kana:8001" if configured else "")), \
                 patch("src.services.kana_reading._predict", side_effect=requests.ConnectionError()):
                info = apply_specialist_reading(data, source, [])
            self.assertEqual(data["person_name_kana"], "")
            self.assertEqual(info["status"], "uncertain")

    def test_single_email_account_is_not_assumed_to_be_a_surname(self):
        data = {"person_name": "風丘 奏太", "person_name_kana": "かぜおか そうなり"}
        source = "風丘 奏太 Souta Kazeoka\nsouta@example.test"
        with patch("src.services.kana_reading._predict", return_value=prediction(["カゼオカタツナリ"], ["カゼオカ"])):
            info = apply_specialist_reading(data, source, [])
        self.assertEqual(data["person_name_kana"], "かぜおか そうた")
        self.assertEqual(info["source"], "roman")

    def test_conflicting_printed_roman_names_do_not_select_the_first(self):
        data = {"person_name": "雲原 晴輔", "person_name_kana": "うんばら せいすけ"}
        source = "雲原 晴輔 Harusuce Kumohara\n雲原 晴輔 Seisuke Kumohara\nkumohara@example.test"
        with patch("src.services.kana_reading._predict", side_effect=self.predict):
            info = apply_specialist_reading(data, source, [])
        self.assertEqual(data["person_name_kana"], "")
        self.assertEqual(info["status"], "uncertain")

    def test_printed_kana_still_outranks_roman_ocr_repairs(self):
        data = {"person_name": "雲原 晴輔", "person_name_kana": "うんばら せいすけ"}
        source = "雲原 晴輔（くもはら はるすけ）\n雲原 晴輔 Harusuce Kumohara\nkumohara@example.test"
        with patch("src.services.kana_reading._predict") as predict:
            info = apply_specialist_reading(data, source, [])
        self.assertEqual(data["person_name_kana"], "くもはら はるすけ")
        self.assertEqual(info["status"], "printed")
        predict.assert_not_called()

    def test_saved_corrections_do_not_override_a_verified_roman_ocr_reading(self):
        source = "雲原 晴輔 Harusuce Kumohara\nkumohara@example.test"
        raw = {"person_name": "雲原 晴輔", "person_name_kana": "うんばら せいすけ", "email": "kumohara@example.test"}
        correction = {"id": "old-reading", "context": raw, "corrected_value": "うんばら せいすけ",
                      "automatic_value": "くもばら さつや", "reading_rules": [], "person_example": True}
        with patch("src.services.extractor._generate_structured_response", return_value=json.dumps(raw)), \
             patch("src.services.extractor.relevant_corrections", return_value=[correction]), \
             patch("src.services.kana_reading._predict", side_effect=self.predict):
            result = extract_card_fields(source, []).data
        self.assertEqual(result["person_name_kana"], "くもはら はるすけ")
        self.assertEqual(result["_automatic"]["person_name_kana"], "くもはら はるすけ")
        self.assertEqual(result["_feedback"]["applied_ids"], [])

    def test_a_token_requiring_two_substitutions_is_not_repaired(self):
        data = {"person_name": "雲原 晴輔", "person_name_kana": "うんばら せいすけ"}
        source = "雲原 晴輔 Harusuxx Kumohara\nkumohara@example.test"
        with patch("src.services.kana_reading._predict", side_effect=self.predict):
            info = apply_specialist_reading(data, source, [])
        self.assertEqual(data["person_name_kana"], "")
        self.assertEqual(info["status"], "uncertain")


if __name__ == "__main__":
    unittest.main()
