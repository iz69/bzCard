import unittest

from src.services.extractor import _refine_person_name_kana, _remove_ungrounded_values, _with_spatial_name_candidates


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

    def test_ruby_blocks_near_the_name_override_a_kana_guess(self):
        data = {"person_name": "青葉 花子", "person_name_kana": "あおば はこ"}
        blocks = [
            {"text": "アオバ", "box": [100, 50, 180, 70]},
            {"text": "ハナコ", "box": [190, 50, 270, 70]},
            {"text": "青葉 花子", "box": [100, 90, 270, 140]},
        ]

        _refine_person_name_kana(data, "青葉 花子", blocks)

        self.assertEqual(data["person_name_kana"], "あおば はなこ")


if __name__ == "__main__":
    unittest.main()
