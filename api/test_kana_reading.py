import unittest
from types import SimpleNamespace
from unittest.mock import patch

import requests

from src.services.kana_reading import apply_specialist_reading
from src.services.extractor import extract_card_fields


class KanaReadingTest(unittest.TestCase):
    def setUp(self):
        self.settings = patch("src.services.kana_reading.settings", SimpleNamespace(kana_base_url="http://kana:8001"))
        self.settings.start()
        self.addCleanup(self.settings.stop)

    def test_full_name_result_is_split_at_a_verified_family_prefix(self):
        response = SimpleNamespace(
            raise_for_status=lambda: None,
            json=lambda: {
                "model": "kanjikana-1.9o",
                "candidates": [{"reading": "ヤガミヒロシ", "log_probability": -0.65}],
                "family_candidates": [{"reading": "ヤガミ", "log_probability": -0.20}],
            },
        )
        data = {"person_name": "矢上 寛", "person_name_kana": ""}
        with patch("src.services.kana_reading.requests.post", return_value=response) as post:
            info = apply_specialist_reading(data, "矢上 寛", [])
        self.assertEqual(data["person_name_kana"], "やがみ ひろし")
        self.assertEqual(info["status"], "applied")
        self.assertEqual(post.call_args.kwargs["json"], {"name": "矢上寛", "family": "矢上"})

    def test_printed_reading_takes_priority(self):
        data = {"person_name": "矢上 寛", "person_name_kana": "やがみ ひろし"}
        with patch("src.services.kana_reading.requests.post") as post:
            info = apply_specialist_reading(data, "矢上 寛（ヤガミ ヒロシ）", [])
        self.assertEqual(info["status"], "printed")
        post.assert_not_called()

    def test_unsegmentable_result_retains_llm_reading(self):
        response = SimpleNamespace(
            raise_for_status=lambda: None,
            json=lambda: {"model": "kanjikana-1.9o", "candidates": [{"reading": "ヤガミヒロシ"}], "family_candidates": [{"reading": "ヤウエ"}]},
        )
        data = {"person_name": "矢上 寛", "person_name_kana": "やがみ ひろし"}
        with patch("src.services.kana_reading.requests.post", return_value=response):
            info = apply_specialist_reading(data, "矢上 寛", [])
        self.assertEqual(data["person_name_kana"], "やがみ ひろし")
        self.assertEqual(info["status"], "fallback")

    def test_unavailable_service_retains_llm_reading(self):
        data = {"person_name": "峠 雅樹", "person_name_kana": ""}
        with patch("src.services.kana_reading.requests.post", side_effect=requests.ConnectionError):
            info = apply_specialist_reading(data, "峠 雅樹", [])
        self.assertEqual(data["person_name_kana"], "")
        self.assertEqual(info["status"], "fallback")

    def test_extraction_keeps_llm_raw_value_and_records_specialist_selection(self):
        response = SimpleNamespace(
            raise_for_status=lambda: None,
            json=lambda: {
                "model": "kanjikana-1.9o",
                "candidates": [{"reading": "トウゲマサキ"}],
                "family_candidates": [{"reading": "トウゲ"}],
            },
        )
        with patch("src.services.extractor._generate_structured_response", return_value='{"person_name":"峠 雅樹","person_name_kana":"とうげ まさる"}'), \
             patch("src.services.kana_reading.requests.post", return_value=response):
            result = extract_card_fields("峠 雅樹", []).data
        self.assertEqual(result["person_name_kana"], "とうげ まさき")
        self.assertEqual(result["_automatic"]["person_name_kana"], "とうげ まさき")
        self.assertEqual(result["_raw"]["person_name_kana"], "とうげ まさる")
        self.assertEqual(result["_model"]["kana"]["status"], "applied")


if __name__ == "__main__":
    unittest.main()
