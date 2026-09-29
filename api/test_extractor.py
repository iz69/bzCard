import unittest

from src.services.extractor import (
    _correct_person_name_order,
    _prefer_labeled_phone_numbers,
    _recover_printed_identity,
    _refine_person_name_kana,
    _remove_ungrounded_values,
    _roman_to_hiragana,
    _with_spatial_name_candidates,
)


class SpatialNameCandidateTests(unittest.TestCase):
    def test_joins_name_blocks_by_horizontal_position(self):
        blocks = [
            {"text": "橋", "box": [470, 376, 629, 478], "_side": "front"},
            {"text": "口", "box": [633, 393, 750, 471], "_side": "front"},
            {"text": "秀 明", "box": [757, 377, 1139, 483], "_side": "front"},
        ]

        source = _with_spatial_name_candidates("橋\n秀 明\n口", blocks)

        self.assertIn("- 橋口秀明", source)
        extracted = {"person_name": "橋口 秀明"}
        _remove_ungrounded_values(extracted, source)
        self.assertEqual(extracted["person_name"], "橋口 秀明")

    def test_does_not_join_blocks_from_opposite_sides(self):
        blocks = [
            {"text": "橋口", "box": [100, 100, 220, 180], "_side": "front"},
            {"text": "秀明", "box": [225, 100, 345, 180], "_side": "back"},
        ]

        self.assertEqual(_with_spatial_name_candidates("", blocks), "")

    def test_roman_name_corrects_a_truncated_kana_guess(self):
        data = {"person_name": "青葉 花子", "person_name_kana": "あおば はこ"}

        _refine_person_name_kana(data, "青葉 花子\nHANAKO AOBA\nhanako.aoba@example.com")

        self.assertEqual(data["person_name_kana"], "あおば はなこ")

    def test_explicit_ruby_overrides_a_kana_guess(self):
        data = {"person_name": "青葉 花子", "person_name_kana": "あおば はこ"}

        _refine_person_name_kana(data, "青葉 花子（アオバ ハナコ）")

        self.assertEqual(data["person_name_kana"], "あおば はなこ")

    def test_printed_name_and_email_override_unrelated_kana(self):
        data = {"person_name": "花咲 彩香", "person_name_kana": "あおば はなこ"}
        ocr = "株式会社花咲プランニング\n花咲 彩香\nAYAKA HANASAKI\nayaka.hanasaki@example.com"

        _refine_person_name_kana(data, ocr)

        self.assertEqual(data["person_name_kana"], "はなさき あやか")

    def test_roman_letters_do_not_drop_a_supported_long_vowel(self):
        data = {"person_name": "桜川 太郎", "person_name_kana": "さくらがわ たろう"}
        ocr = "桜川 太郎\nTARO SAKURAGAWA\ntaro.sakuragawa@example.com"

        _refine_person_name_kana(data, ocr)

        self.assertEqual(data["person_name_kana"], "さくらがわ たろう")

    def test_common_romanized_given_names_keep_natural_long_vowels(self):
        self.assertEqual(_roman_to_hiragana("RYO"), "りょう")
        self.assertEqual(_roman_to_hiragana("SHOTA"), "しょうた")
        self.assertEqual(_roman_to_hiragana("KENICHI"), "けんいち")

    def test_ocr_name_and_company_correct_model_role_confusion(self):
        data = {"person_name": "ひだまり メディア 株式会社", "company_name": "HIDAMARI MEDIA"}
        ocr = "ひだまりメディア株式会社\nHIDAMARI MEDIA\n編集部 編集者\n日向 京\nRYO HINATA\nryo.hinata@example.com"

        _recover_printed_identity(data, ocr)

        self.assertEqual(data["person_name"], "日向 京")
        self.assertEqual(data["company_name"], "ひだまりメディア株式会社")

    def test_company_is_recovered_when_model_transliterates_it(self):
        data = {"person_name": "北斗 誠", "company_name": "ホクトエンジニアリング"}

        _recover_printed_identity(data, "株式会社北斗エンジニアリング\n北斗 誠\nMAKOTO HOKUTO\nmakoto.hokuto@example.com")

        self.assertEqual(data["company_name"], "株式会社北斗エンジニアリング")

    def test_unrelated_roman_words_do_not_replace_a_person(self):
        data = {"person_name": "こもれびサービス株式会社", "company_name": ""}

        _recover_printed_identity(data, "こもれびサービス株式会社\n木森 拓也\nTAKUYA KIMORI\ninfo@example.com")

        self.assertEqual(data["person_name"], "こもれびサービス株式会社")

    def test_ruby_blocks_near_the_name_override_a_kana_guess(self):
        data = {"person_name": "青葉 花子", "person_name_kana": "あおば はこ"}
        blocks = [
            {"text": "アオバ", "box": [100, 50, 180, 70]},
            {"text": "ハナコ", "box": [190, 50, 270, 70]},
            {"text": "青葉 花子", "box": [100, 90, 270, 140]},
        ]

        _refine_person_name_kana(data, "青葉 花子", blocks)

        self.assertEqual(data["person_name_kana"], "あおば はなこ")

    def test_yagami_name_order_and_company_punctuation(self):
        blocks = [
            {"text": "寛", "box": [1106, 591, 1367, 694], "_side": "front"},
            {"text": "矢上", "box": [861, 596, 1126, 697], "_side": "front"},
        ]
        data = {
            "person_name": "寛 矢上",
            "person_name_kana": "わか や上",
            "company_name": "アイ・ディ・ケイ株式会社",
        }
        raw = "寛\n矢上\nアイ·ディ·ケイ株式会社\nHIROSHI YAGAMI\nh-yagami@i-d-k.com"
        _correct_person_name_order(data, blocks)
        _refine_person_name_kana(data, raw, blocks)
        _remove_ungrounded_values(data, _with_spatial_name_candidates(raw, blocks))
        self.assertEqual(data["person_name"], "矢上 寛")
        self.assertEqual(data["person_name_kana"], "やがみ ひろし")
        self.assertEqual(data["company_name"], "アイ・ディ・ケイ株式会社")

    def test_multiple_numbers_on_one_line_remain_individual_evidence(self):
        raw = "TEL 0463-35-9650 FAX 0463-35-9763 携帯 080-6885-1369"
        data = {"tel": "0463-35-9650", "fax": "0463-35-9763", "mobile": "080-6885-1369"}
        _remove_ungrounded_values(data, raw)
        self.assertEqual({key: data[key] for key in ("tel", "fax", "mobile")}, {"tel": "0463-35-9650", "fax": "0463-35-9763", "mobile": "080-6885-1369"})

    def test_english_side_phone_labels_correct_swapped_numbers(self):
        raw = "045-571-6322\nTel.+81-45-570-5300 Fax.+81-45-571-6322"
        data = {"tel": "045-571-6322", "fax": "045-571-6322"}
        _prefer_labeled_phone_numbers(data, raw)
        _remove_ungrounded_values(data, raw)
        self.assertEqual(data["tel"], "045-570-5300")
        self.assertEqual(data["fax"], "045-571-6322")


if __name__ == "__main__":
    unittest.main()
