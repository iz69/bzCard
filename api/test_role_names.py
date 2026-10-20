"""Keep printed titles separate from complete and fragmented names."""
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from src.services.extractor import extract_card_fields
from src.services.name_evidence import _with_spatial_name_candidates, person_name_is_printed


class RoleNameTests(unittest.TestCase):
    def extract(self, source, blocks, raw, previous=None):
        with patch("src.services.extractor._generate_structured_response", return_value=json.dumps(raw)), \
             patch("src.services.kana_reading.settings", SimpleNamespace(kana_base_url="")):
            return extract_card_fields(source, blocks, previous=previous).data

    def test_fragmented_row_excludes_the_role_from_name_and_title(self):
        blocks = [{"text": text, "box": box, "_side": "front"} for text, box in (
            ("42 サンプル電機株式会社", [83, 40, 650, 102]),
            ("品質保証部", [295, 154, 449, 181]),
            ("主任 月 丘", [127, 201, 479, 253]),
            ("景", [473, 202, 616, 256]), ("輔", [706, 209, 750, 252]),
        )]
        raw = {"person_name": "サンプル 電機株式会社", "company_name": "サンプル 電機株式会社",
               "department": "品質保証部", "title": "主任 月丘景輔", "email": "tsukioka@example.test"}
        source = "\n".join(block["text"] for block in blocks) + "\ntsukioka@example.test"
        previous = {"person_name": "主任月丘景輔", "email": "tsukioka@example.test"}
        for old in (None, previous):
            for scale in (.5, 1, 3):
                scaled = [{**block, "box": [coordinate * scale for coordinate in block["box"]]} for block in blocks]
                with self.subTest(previous=bool(old), scale=scale):
                    result = self.extract(source, scaled, raw, previous=old)
                    self.assertEqual(result["person_name"], "月丘景輔")
                    self.assertEqual(result["title"], "主任")
                    self.assertEqual(result["department"], "品質保証部")
                    self.assertEqual(result["company_name"], "サンプル電機 株式会社")
                    self.assertEqual(result["_raw"], raw)
                hint = _with_spatial_name_candidates(source, scaled).split("【座標補正による氏名候補】")[-1]
                self.assertIn("月丘景輔", hint)
                self.assertNotIn("主任", hint)
                self.assertFalse(person_name_is_printed("主任月丘景輔", source, scaled))

    def test_varied_titles_are_separated_without_person_specific_rules(self):
        for role, name in (("主任", "月丘 景輔"), ("課長", "朝日 智子"),
                           ("代表取締役社長", "長谷川 亮"), ("研究員", "秦 嘉一郎"),
                           ("グループ長", "星路 奏文")):
            source = f"{role} {name}"
            for proposed in (role + name.replace(" ", ""), name, ""):
                with self.subTest(role=role, proposed=proposed):
                    result = self.extract(source, [], {"person_name": proposed, "title": source})
                self.assertEqual(result["person_name"], name)
                self.assertEqual(result["title"], role)

    def test_title_after_person_is_removed_only_when_a_known_role_remains(self):
        for title, expected in (("朝日 智子 課長", "課長"), ("課長 朝日 智子", "課長"),
                                ("主任研究員", "主任研究員")):
            source = f"朝日 智子\n{title}"
            result = self.extract(source, [], {"person_name": "朝日 智子", "title": title})
            self.assertEqual(result["title"], expected)

    def test_partial_name_is_removed_from_the_exact_printed_role_row(self):
        for title in ("主任 月 丘", "主任 月丘"):
            source = "主任 月 丘\n景\n輔\n月丘 景輔"
            result = self.extract(source, [], {"person_name": "月丘 景輔", "title": title})
            self.assertEqual(result["title"], "主任")
        # A department role and a separate person's surname do not form a
        # printed title/name row; substring presence alone is insufficient.
        result = self.extract("主任研究員\n月丘 景輔", [],
                              {"person_name": "月丘 景輔", "title": "主任研究員"})
        self.assertEqual(result["title"], "主任研究員")

    def test_unseparated_name_prefix_is_not_cut_and_departments_remain_excluded(self):
        self.assertTrue(person_name_is_printed("主任子", "主任子", []))
        result = self.extract("主任 営業部", [], {"person_name": "主任 営業部"})
        self.assertEqual(result["person_name"], "")

    def test_roles_do_not_connect_people_on_different_rows_or_sides(self):
        for side, y in (("back", 20), ("front", 120)):
            blocks = [{"text": "主任 月 丘", "box": [10, 20, 200, 70], "_side": "front"},
                      {"text": "景輔", "box": [220, y, 320, y + 50], "_side": side}]
            source = "\n".join(block["text"] for block in blocks)
            self.assertFalse(person_name_is_printed("月丘景輔", source, blocks))

    def test_company_spacing_follows_ocr_without_including_logo_text(self):
        for source, model, expected in (
            ("42 サンプル電機株式会社", "サンプル 電機株式会社", "サンプル電機 株式会社"),
            ("77 株式会社朝日工業", "株式会社 朝日 工業", "株式会社 朝日工業"),
            ("有限会社 BLUE SKY", "有限会社 BLUE SKY", "有限会社 BLUE SKY"),
            ("株式会社 青葉 技術", "株式会社青葉技術", "株式会社 青葉 技術"),
            ("株式会社88電機", "株式会社 88電機", "株式会社 88電機"),
        ):
            with self.subTest(source=source):
                result = self.extract(source, [], {"company_name": model})
                self.assertEqual(result["company_name"], expected)


if __name__ == "__main__":
    unittest.main()
