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
                "candidates": [{"reading": "ナツモリリョウ", "log_probability": -0.65}],
                "family_candidates": [{"reading": "ナツモリ", "log_probability": -0.20}],
            },
        )
        data = {"person_name": "夏森 綾", "person_name_kana": ""}
        with patch("src.services.kana_reading.requests.post", return_value=response) as post:
            info = apply_specialist_reading(data, "夏森 綾", [])
        self.assertEqual(data["person_name_kana"], "なつもり りょう")
        self.assertEqual(info["status"], "applied")
        self.assertEqual(post.call_args.kwargs["json"], {"name": "夏森綾", "family": "夏森"})

    def test_printed_reading_takes_priority(self):
        data = {"person_name": "夏森 綾", "person_name_kana": "なつもり りょう"}
        with patch("src.services.kana_reading.requests.post") as post:
            info = apply_specialist_reading(data, "夏森 綾（ナツモリ リョウ）", [])
        self.assertEqual(info["status"], "printed")
        post.assert_not_called()

    def test_unsegmentable_result_retains_llm_reading(self):
        response = SimpleNamespace(
            raise_for_status=lambda: None,
            json=lambda: {"model": "kanjikana-1.9o", "candidates": [{"reading": "ナツモリリョウ"}], "family_candidates": [{"reading": "ナツバヤシ"}]},
        )
        data = {"person_name": "夏森 綾", "person_name_kana": "なつもり りょう"}
        with patch("src.services.kana_reading.requests.post", return_value=response):
            info = apply_specialist_reading(data, "夏森 綾", [])
        self.assertEqual(data["person_name_kana"], "なつもり りょう")
        self.assertEqual(info["status"], "fallback")

    def test_unavailable_service_retains_llm_reading(self):
        data = {"person_name": "凪 芽衣", "person_name_kana": ""}
        with patch("src.services.kana_reading.requests.post", side_effect=requests.ConnectionError):
            info = apply_specialist_reading(data, "凪 芽衣", [])
        self.assertEqual(data["person_name_kana"], "")
        self.assertEqual(info["status"], "fallback")

    def test_extraction_keeps_llm_raw_value_and_records_specialist_selection(self):
        response = SimpleNamespace(
            raise_for_status=lambda: None,
            json=lambda: {
                "model": "kanjikana-1.9o",
                "candidates": [{"reading": "ナギメイ"}],
                "family_candidates": [{"reading": "ナギ"}],
            },
        )
        with patch("src.services.extractor._generate_structured_response", return_value='{"person_name":"凪 芽衣","person_name_kana":"なぎ めお"}'), \
             patch("src.services.kana_reading.requests.post", return_value=response):
            result = extract_card_fields("凪 芽衣", []).data
        self.assertEqual(result["person_name_kana"], "なぎ めい")
        self.assertEqual(result["_automatic"]["person_name_kana"], "なぎ めい")
        self.assertEqual(result["_raw"]["person_name_kana"], "なぎ めお")
        self.assertEqual(result["_model"]["kana"]["status"], "applied")

    def test_spaced_kanji_name_uses_specialist_for_email_short_vowel(self):
        response = SimpleNamespace(
            raise_for_status=lambda: None,
            json=lambda: {
                "model": "kanjikana-1.9o",
                "candidates": [{"reading": "ユメノアキオミ"}],
                "family_candidates": [{"reading": "ユメノ"}],
            },
        )
        model_output = '{"person_name":"夢 野 暁 臣","person_name_kana":"さふじ つねつと しゅん"}'
        ocr = "品質保証部\n夢 野 暁 臣\nakiomi.yumeno@example.test"
        with patch("src.services.extractor._generate_structured_response", return_value=model_output), \
             patch("src.services.kana_reading.requests.post", return_value=response) as post:
            result = extract_card_fields(ocr, []).data

        self.assertEqual(result["person_name"], "夢野 暁臣")
        self.assertEqual(result["person_name_kana"], "ゆめの あきおみ")
        self.assertEqual(result["_raw"]["person_name"], "夢 野 暁 臣")
        self.assertEqual(result["_model"]["kana"]["status"], "roman")
        self.assertEqual(post.call_args.kwargs["json"], {"name": "夢野暁臣", "family": "夢野"})

    def test_partial_surname_spacing_is_repaired_before_reading_prediction(self):
        response = SimpleNamespace(
            raise_for_status=lambda: None,
            json=lambda: {
                "model": "kanjikana-1.9o",
                "candidates": [{"reading": "カゼオカソウタ"}],
                "family_candidates": [{"reading": "カゼオカ"}],
            },
        )
        blocks = [
            {"text": "奏太", "box": [1528, 727, 2160, 896], "_side": "front"},
            {"text": "風 丘", "box": [861, 729, 1397, 896], "_side": "front"},
        ]
        output = '{"person_name":"風 丘 奏太","person_name_kana":"かぜおか そうた"}'
        with patch("src.services.extractor._generate_structured_response", return_value=output), \
             patch("src.services.kana_reading.requests.post", return_value=response) as post:
            result = extract_card_fields("奏太\n風 丘", blocks).data

        self.assertEqual(result["person_name"], "風丘 奏太")
        self.assertEqual(result["person_name_kana"], "かぜおか そうた")
        self.assertEqual(result["_automatic"]["person_name"], "風丘 奏太")
        self.assertEqual(result["_raw"]["person_name"], "風 丘 奏太")
        self.assertEqual(post.call_args.kwargs["json"], {"name": "風丘奏太", "family": "風丘"})

    def test_continuous_name_uses_printed_roman_name_with_email_suffix(self):
        response = SimpleNamespace(
            raise_for_status=lambda: None,
            json=lambda: {
                "model": "kanjikana-1.9o",
                "candidates": [{"reading": "ハナサキソウキ"}, {"reading": "ハナサキソウジュ"}],
                "family_candidates": [{"reading": "ハナサキ"}],
            },
        )
        ocr = "花崎蒼樹\nsouju.hanasaki.az@example.test\n\n【裏面】\nSouju Hanasaki"
        output = '{"person_name":"花崎蒼樹","person_name_kana":"ふじはだき"}'
        with patch("src.services.extractor._generate_structured_response", return_value=output), \
             patch("src.services.kana_reading.requests.post", return_value=response) as post:
            result = extract_card_fields(ocr, []).data

        self.assertEqual(result["person_name"], "花崎 蒼樹")
        self.assertEqual(result["person_name_kana"], "はなさき そうじゅ")
        self.assertEqual(result["_model"]["kana"]["status"], "roman")
        self.assertEqual(post.call_args.kwargs["json"], {"name": "花崎蒼樹", "family": "花崎"})

    def test_continuous_name_without_matching_email_needs_full_model_support(self):
        ocr = "花崎蒼樹\nunrelated@example.test\nSouju Hanasaki"
        output = '{"person_name":"花崎蒼樹","person_name_kana":""}'
        response = SimpleNamespace(raise_for_status=lambda: None, json=lambda: {
            "model": "kanjikana-1.9o", "candidates": [{"reading": "ハナサキソウキ"}],
            "family_candidates": [{"reading": "ハナサキ"}],
        })
        with patch("src.services.extractor._generate_structured_response", return_value=output), \
             patch("src.services.kana_reading.requests.post", return_value=response) as post:
            result = extract_card_fields(ocr, []).data

        self.assertEqual(result["person_name"], "花崎蒼樹")
        self.assertEqual(result["person_name_kana"], "")
        post.assert_any_call("http://kana:8001/predict", json={"name": "花崎蒼樹", "family": "花崎"}, timeout=30)

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
                "candidates": [{"reading": "ハナサキソウジュ"}],
                "family_candidates": [{"reading": "ハナサキ"}],
            },
        )
        data = {"person_name": "花崎蒼樹", "person_name_kana": "はなさき そうじゅ"}
        ocr = "花崎蒼樹（ハナサキ ソウジュ）\nSouju Hanasaki\nsouju.hanasaki.az@example.test"
        with patch("src.services.kana_reading.requests.post", return_value=response):
            info = apply_specialist_reading(data, ocr, [])

        self.assertEqual(data["person_name"], "花崎 蒼樹")
        self.assertEqual(data["person_name_kana"], "はなさき そうじゅ")
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
