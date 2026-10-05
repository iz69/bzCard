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

    def test_spaced_kanji_name_uses_specialist_for_email_short_vowel(self):
        response = SimpleNamespace(
            raise_for_status=lambda: None,
            json=lambda: {
                "model": "kanjikana-1.9o",
                "candidates": [{"reading": "サトウアツトシ"}],
                "family_candidates": [{"reading": "サトウ"}],
            },
        )
        model_output = '{"person_name":"佐 藤 敦 俊","person_name_kana":"さふじ つねつと しゅん"}'
        ocr = "品質保証部\n佐 藤 敦 俊\natsutoshi.sato@example.test"
        with patch("src.services.extractor._generate_structured_response", return_value=model_output), \
             patch("src.services.kana_reading.requests.post", return_value=response) as post:
            result = extract_card_fields(ocr, []).data

        self.assertEqual(result["person_name"], "佐藤 敦俊")
        self.assertEqual(result["person_name_kana"], "さとう あつとし")
        self.assertEqual(result["_raw"]["person_name"], "佐 藤 敦 俊")
        self.assertEqual(result["_model"]["kana"]["status"], "roman")
        self.assertEqual(post.call_args.kwargs["json"], {"name": "佐藤敦俊", "family": "佐藤"})

    def test_partial_surname_spacing_is_repaired_before_reading_prediction(self):
        response = SimpleNamespace(
            raise_for_status=lambda: None,
            json=lambda: {
                "model": "kanjikana-1.9o",
                "candidates": [{"reading": "ツルカワタツヤ"}],
                "family_candidates": [{"reading": "ツルカワ"}],
            },
        )
        blocks = [
            {"text": "達也", "box": [1528, 727, 2160, 896], "_side": "front"},
            {"text": "鶴 川", "box": [861, 729, 1397, 896], "_side": "front"},
        ]
        output = '{"person_name":"鶴 川 達也","person_name_kana":"つるかわ たつや"}'
        with patch("src.services.extractor._generate_structured_response", return_value=output), \
             patch("src.services.kana_reading.requests.post", return_value=response) as post:
            result = extract_card_fields("達也\n鶴 川", blocks).data

        self.assertEqual(result["person_name"], "鶴川 達也")
        self.assertEqual(result["person_name_kana"], "つるかわ たつや")
        self.assertEqual(result["_automatic"]["person_name"], "鶴川 達也")
        self.assertEqual(result["_raw"]["person_name"], "鶴 川 達也")
        self.assertEqual(post.call_args.kwargs["json"], {"name": "鶴川達也", "family": "鶴川"})

    def test_continuous_name_uses_printed_roman_name_with_email_suffix(self):
        response = SimpleNamespace(
            raise_for_status=lambda: None,
            json=lambda: {
                "model": "kanjikana-1.9o",
                "candidates": [{"reading": "ハガダイキ"}, {"reading": "ハガダイジュ"}],
                "family_candidates": [{"reading": "ハガ"}],
            },
        )
        ocr = "芳賀大樹\ndaiju.haga.az@example.test\n\n【裏面】\nDaiju Haga"
        output = '{"person_name":"芳賀大樹","person_name_kana":"ふじはだき"}'
        with patch("src.services.extractor._generate_structured_response", return_value=output), \
             patch("src.services.kana_reading.requests.post", return_value=response) as post:
            result = extract_card_fields(ocr, []).data

        self.assertEqual(result["person_name"], "芳賀 大樹")
        self.assertEqual(result["person_name_kana"], "はが だいじゅ")
        self.assertEqual(result["_model"]["kana"]["status"], "roman")
        self.assertEqual(post.call_args.kwargs["json"], {"name": "芳賀大樹", "family": "芳賀"})

    def test_continuous_name_needs_matching_email_and_model_reading(self):
        ocr = "芳賀大樹\nunrelated@example.test\nDaiju Haga"
        output = '{"person_name":"芳賀大樹","person_name_kana":""}'
        with patch("src.services.extractor._generate_structured_response", return_value=output), \
             patch("src.services.kana_reading.requests.post") as post:
            result = extract_card_fields(ocr, []).data

        self.assertEqual(result["person_name"], "芳賀大樹")
        self.assertEqual(result["person_name_kana"], "")
        post.assert_not_called()

    def test_continuous_name_does_not_assume_two_character_surname(self):
        def predict(_url, *, json, timeout):
            family = json["family"]
            return SimpleNamespace(
                raise_for_status=lambda: None,
                json=lambda: {
                    "model": "kanjikana-1.9o",
                    "candidates": [{"reading": "ハタタイチロ"}],
                    "family_candidates": [{"reading": "ハタタ" if family == "秦太" else "ハタ"}],
                },
            )

        data = {"person_name": "秦太一郎", "person_name_kana": ""}
        ocr = "秦太一郎\nTaichiro Hata\ntaichiro.hata@example.test"
        with patch("src.services.kana_reading.requests.post", side_effect=predict) as post:
            info = apply_specialist_reading(data, ocr, [])

        self.assertEqual(data["person_name"], "秦 太一郎")
        self.assertEqual(data["person_name_kana"], "はた たいちろ")
        self.assertEqual(info["status"], "roman")
        self.assertEqual(post.call_count, 2)

    def test_continuous_name_preserves_printed_kana(self):
        response = SimpleNamespace(
            raise_for_status=lambda: None,
            json=lambda: {
                "model": "kanjikana-1.9o",
                "candidates": [{"reading": "ハガダイジュ"}],
                "family_candidates": [{"reading": "ハガ"}],
            },
        )
        data = {"person_name": "芳賀大樹", "person_name_kana": "はが だいじゅ"}
        ocr = "芳賀大樹（ハガ ダイジュ）\nDaiju Haga\ndaiju.haga.az@example.test"
        with patch("src.services.kana_reading.requests.post", return_value=response):
            info = apply_specialist_reading(data, ocr, [])

        self.assertEqual(data["person_name"], "芳賀 大樹")
        self.assertEqual(data["person_name_kana"], "はが だいじゅ")
        self.assertEqual(info["status"], "printed")

    def test_email_reading_blocks_incompatible_specialist_guess(self):
        response = SimpleNamespace(
            raise_for_status=lambda: None,
            json=lambda: {
                "model": "kanjikana-1.9o",
                "candidates": [{"reading": "アオバノブオ"}],
                "family_candidates": [{"reading": "アオバ"}],
            },
        )
        data = {"person_name": "青葉 伸一", "person_name_kana": "あおば しんいち"}
        with patch("src.services.kana_reading.requests.post", return_value=response):
            info = apply_specialist_reading(data, "青葉 伸一\nshinichi.aoba@example.test", [])
        self.assertEqual(data["person_name_kana"], "あおば しんいち")
        self.assertEqual(info["status"], "roman")


if __name__ == "__main__":
    unittest.main()
