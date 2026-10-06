import json
import random
import unittest
from types import SimpleNamespace
from src.services.normalization import _normalize_company_name
from unittest.mock import patch

from src.services.extractor import (
    _prefer_labeled_phone_numbers,
    _remove_ungrounded_values,
    _separate_department_and_title,
    extract_card_fields,
)
from src.services.name_evidence import _roman_to_hiragana, _with_spatial_name_candidates
from src.services.person_identity import (
    _correct_person_name_order,
    _join_spaced_person_name,
    _recover_printed_identity,
    _refine_person_name_kana,
)


def _generated_identity(seed: int) -> dict[str, str]:
    """Build reproducible, fictitious OCR fields without copying a card."""
    rng = random.Random(seed)

    def kanji(start: int) -> str:
        return "".join(chr(start + rng.randrange(256)) for _ in range(2))

    def roman() -> str:
        return "".join(rng.choice("kmnr") + rng.choice("aeiou") for _ in range(2)).upper()

    family, given = kanji(0x4E00), kanji(0x5200)
    family_roman, given_roman = roman(), roman()
    return {
        "name": f"{family} {given}",
        "kana": f"{_roman_to_hiragana(family_roman)} {_roman_to_hiragana(given_roman)}",
        "roman": f"{family_roman} {given_roman}",
        "family_roman": family_roman,
        "given_roman": given_roman,
        "email": f"{given_roman[0].lower()}.{family_roman.lower()}@example.test",
        "store": f"{kanji(0x5600)}店",
        "company": f"{kanji(0x5A00)}サービス",
    }


class SpatialNameCandidateTests(unittest.TestCase):
    def test_partial_spacing_uses_printed_boundary_for_one_character_surname(self):
        data = {"person_name": "林 太 一郎"}
        blocks = [
            {"text": "林", "box": [100, 100, 160, 160], "_side": "front"},
            {"text": "太 一郎", "box": [190, 100, 370, 160], "_side": "front"},
        ]
        _join_spaced_person_name(data, "林\n太 一郎", blocks)
        self.assertEqual(data["person_name"], "林 太一郎")

    def test_partial_spacing_requires_name_blocks_on_same_side_and_line(self):
        for side, y in (("back", 100), ("front", 300)):
            with self.subTest(side=side, y=y):
                data = {"person_name": "鶴 川 達也"}
                blocks = [
                    {"text": "鶴 川", "box": [100, 100, 220, 160], "_side": "front"},
                    {"text": "達也", "box": [250, y, 370, y + 60], "_side": side},
                ]
                _join_spaced_person_name(data, "鶴 川\n達也", blocks)
                self.assertEqual(data["person_name"], "鶴川達也")

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

    def test_romanized_n_before_i_keeps_the_nasal_in_given_names(self):
        for roman, kana in (("SHINICHI", "しんいち"), ("JUNICHI", "じゅんいち"),
                            ("SHUNICHI", "しゅんいち")):
            with self.subTest(roman=roman):
                self.assertEqual(_roman_to_hiragana(roman), kana)

    def test_email_romanization_does_not_replace_correct_shinichi_reading(self):
        ocr = "青葉 伸一\nshinichi.aoba@example.test"
        output = json.dumps({"person_name": "青葉 伸一", "person_name_kana": "あおば しんいち"},
                            ensure_ascii=False)
        with patch("src.services.extractor._generate_structured_response", return_value=output):
            result = extract_card_fields(ocr, []).data

        self.assertEqual(result["person_name_kana"], "あおば しんいち")
        self.assertEqual(result["_automatic"]["person_name_kana"], "あおば しんいち")

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

    def test_printed_person_is_recovered_independently_of_unmatched_roman_words(self):
        data = {"person_name": "こもれびサービス株式会社", "company_name": ""}

        _recover_printed_identity(data, "こもれびサービス株式会社\n木森 拓也\nTAKUYA KIMORI\ninfo@example.com")

        self.assertEqual(data["person_name"], "木森 拓也")
        self.assertEqual(data["company_name"], "こもれびサービス株式会社")
        _refine_person_name_kana(data, "木森 拓也\nTAKUYA KIMORI\ninfo@example.com")
        self.assertEqual(data["person_name_kana"], "")

    def test_split_name_with_ruby_replaces_a_company_in_person_field(self):
        ocr = "株式会社エフアンドエム\nたけ\nや\nおお\nまさ\n将 矢\n大 竹\nmasaya_otake@example.test"
        blocks = [
            {"text": "おお", "box": [836, 739, 942, 795], "_side": "front"},
            {"text": "たけ", "box": [1142, 737, 1248, 795], "_side": "front"},
            {"text": "まさ", "box": [1523, 741, 1626, 791], "_side": "front"},
            {"text": "や", "box": [1848, 737, 1906, 786], "_side": "front"},
            {"text": "将 矢", "box": [1492, 777, 1955, 933], "_side": "front"},
            {"text": "大 竹", "box": [812, 786, 1269, 933], "_side": "front"},
        ]
        output = json.dumps({"person_name": "株式会社 エフアンドエム",
                             "person_name_kana": "エフアンドエム ジェイピーエックス",
                             "company_name": "株式会社 エフアンドエム"}, ensure_ascii=False)
        with patch("src.services.extractor._generate_structured_response", return_value=output):
            result = extract_card_fields(ocr, blocks).data

        self.assertEqual(result["person_name"], "大竹 将矢")
        self.assertEqual(result["person_name_kana"], "おおたけ まさや")
        self.assertEqual(result["company_name"], "株式会社 エフアンドエム")
        self.assertEqual(result["_raw"]["person_name"], "株式会社 エフアンドエム")

    def test_large_four_kanji_name_replaces_latin_logo_in_person_field(self):
        ocr = "TOYOTA\n平塚営業所 秦野出張所 営業担当\nL&F\n三 木 洋 亮\n物流システム\nトヨタL&F神奈川株式会社\nmiki@toyota-if-kanagawa.co.jp"
        blocks = [
            {"text": "TOYOTA", "box": [807, 49, 1021, 99], "font_size": 50, "_side": "front"},
            {"text": "三 木 洋 亮", "box": [126, 198, 527, 261], "font_size": 63, "_side": "front"},
            {"text": "トヨタL&F神奈川株式会社", "box": [125, 324, 714, 375], "font_size": 51, "_side": "front"},
        ]
        output = json.dumps({"person_name": "TOYOTA", "person_name_kana": "トヨタ",
                             "company_name": "L&F"}, ensure_ascii=False)
        response = SimpleNamespace(raise_for_status=lambda: None, json=lambda: {
            "candidates": [{"reading": "ミキヒロアキ"}], "family_candidates": [{"reading": "ミキ"}]})
        with patch("src.services.extractor._generate_structured_response", return_value=output), \
             patch("src.services.kana_reading.settings", SimpleNamespace(kana_base_url="http://kana:8001")), \
             patch("src.services.kana_reading.requests.post", return_value=response):
            result = extract_card_fields(ocr, blocks).data

        self.assertEqual(result["person_name"], "三木 洋亮")
        self.assertEqual(result["company_name"], "トヨタL&F神奈川 株式会社")
        self.assertEqual(result["_raw"]["person_name"], "TOYOTA")

    def test_rescan_retains_supported_reading_for_same_person(self):
        ocr = "TOYOTA\n三 木 洋 亮\nトヨタL&F神奈川株式会社\nmiki@example.test"
        blocks = [{"text": "三 木 洋 亮", "box": [126, 198, 527, 261],
                   "font_size": 63, "_side": "front"}]
        output = '{"person_name":"TOYOTA","person_name_kana":"トヨタ","email":"miki@example.test"}'
        previous = {"person_name": "三木 洋亮", "person_name_kana": "みき ようすけ",
                    "email": "miki@example.test", "mobile": ""}

        response = SimpleNamespace(raise_for_status=lambda: None, json=lambda: {
            "candidates": [{"reading": "ミキヒロアキ"}, {"reading": "ミキヨウスケ"}],
            "family_candidates": [{"reading": "ミキ"}]})
        with patch("src.services.extractor._generate_structured_response", return_value=output), \
             patch("src.services.kana_reading.settings", SimpleNamespace(kana_base_url="http://kana:8001")), \
             patch("src.services.kana_reading.requests.post", return_value=response):
            result = extract_card_fields(ocr, blocks, previous=previous).data
            unrelated = extract_card_fields(ocr, blocks, previous={**previous, "email": "other@example.test"}).data

        self.assertEqual(result["person_name"], "三木 洋亮")
        self.assertEqual(result["person_name_kana"], "みき ようすけ")
        self.assertTrue(result["_model"]["kana"]["previous_reading_retained"])
        self.assertEqual(unrelated["person_name_kana"], "みき ひろあき")

    def test_parenthesized_area_code_keeps_printed_tel_and_fax(self):
        data = {"tel": "0465-81-5877", "fax": "0465-81-5885"}

        _remove_ungrounded_values(data, "TEL(0465)-81-5877 FAX(0465)-81-5885")

        self.assertEqual(data["tel"], "0465-81-5877")
        self.assertEqual(data["fax"], "0465-81-5885")

    def test_printed_offices_replace_a_product_label_in_department(self):
        data = {"department": "物流システム", "title": "営業担当"}

        _separate_department_and_title(data, "平塚営業所 秦野出張所 営業担当\n物流システム")

        self.assertEqual(data["department"], "平塚営業所 秦野出張所")
        self.assertEqual(data["title"], "営業担当")

    def test_store_name_is_replaced_using_printed_name_and_email_initial(self):
        case = _generated_identity(1)
        ocr = "\n".join((case["store"], "営業担当", case["name"], case["roman"],
                         case["company"], case["email"]))
        output = json.dumps({
            "person_name": case["store"], "person_name_kana": case["kana"].split()[0] + " てん",
            "company_name": case["company"], "department": case["store"].replace("店", "部"),
            "title": "営業担当",
        }, ensure_ascii=False)
        response = SimpleNamespace(raise_for_status=lambda: None, json=lambda: {
            "candidates": [{"reading": case["kana"].replace(" ", "")}],
            "family_candidates": [{"reading": case["kana"].split()[0]}]})
        with patch("src.services.extractor._generate_structured_response", return_value=output), \
             patch("src.services.kana_reading.settings", SimpleNamespace(kana_base_url="http://kana:8001")), \
             patch("src.services.kana_reading.requests.post", return_value=response):
            result = extract_card_fields(ocr, []).data

        self.assertEqual(result["person_name"], case["name"])
        self.assertEqual(result["person_name_kana"], case["kana"])
        self.assertEqual(result["company_name"], case["company"])
        self.assertEqual(result["department"], case["store"])

    def test_store_name_moves_to_empty_department(self):
        case = _generated_identity(2)
        store = case["store"].replace("店", "支店")
        data = {"person_name": store, "department": ""}
        _recover_printed_identity(data, "\n".join((store, case["name"], case["roman"], case["email"])))
        self.assertEqual(data["person_name"], case["name"])
        self.assertEqual(data["department"], store)

    def test_existing_department_is_preserved(self):
        case = _generated_identity(3)
        department = case["store"].replace("店", "部")
        data = {"person_name": case["store"], "department": department}
        _recover_printed_identity(data, "\n".join((case["store"], department, case["name"],
                                                    case["roman"], case["email"])))
        self.assertEqual(data["department"], department)

    def test_reversed_email_name_order_corroborates_printed_name(self):
        case = _generated_identity(4)
        data = {"person_name": case["store"]}
        email = f"{case['given_roman'].lower()}.{case['family_roman'].lower()}@example.test"
        _recover_printed_identity(data, "\n".join((case["store"], case["name"],
                                                    case["roman"], email)))
        self.assertEqual(data["person_name"], case["name"])

    def test_family_first_email_does_not_reverse_a_known_reading(self):
        case = _generated_identity(5)
        data = {"person_name": case["name"], "person_name_kana": case["kana"]}
        email = f"{case['family_roman'].lower()}.{case['given_roman'].lower()}@example.test"
        _refine_person_name_kana(data, "\n".join((case["name"], case["roman"], email)))
        self.assertEqual(data["person_name_kana"], case["kana"])

    def test_mismatched_initial_does_not_supply_a_reading_for_the_printed_name(self):
        case = _generated_identity(6)
        data = {"person_name": case["store"]}
        wrong_initial = "z" if case["given_roman"][0] != "Z" else "q"
        email = f"{wrong_initial}.{case['family_roman'].lower()}@example.test"
        _recover_printed_identity(data, "\n".join((case["store"], case["name"],
                                                    case["roman"], email)))
        self.assertEqual(data["person_name"], case["name"])
        _refine_person_name_kana(data, "\n".join((case["name"], case["roman"], email)))
        self.assertEqual(data["person_name_kana"], "")

    def test_ambiguous_printed_people_reject_a_store_as_person_name(self):
        first, second = _generated_identity(7), _generated_identity(8)
        data = {"person_name": first["store"]}
        ocr = "\n".join((first["store"], first["name"], first["roman"], first["email"],
                         second["name"], second["roman"], second["email"]))
        _recover_printed_identity(data, ocr)
        self.assertEqual(data["person_name"], "")

    def test_correct_person_is_preserved(self):
        first, second = _generated_identity(9), _generated_identity(10)
        data = {"person_name": second["name"], "person_name_kana": second["kana"]}
        _recover_printed_identity(data, "\n".join((first["store"], first["name"],
                                                    first["roman"], first["email"], second["name"])))
        self.assertEqual(data["person_name"], second["name"])
        self.assertEqual(data["person_name_kana"], second["kana"])

    def test_printed_department_and_title_remove_overlap(self):
        data = {"department": "経営企画室", "title": "経営企画室 室長"}

        _separate_department_and_title(data, "経営企画室 室長\n遠山 千佳")

        self.assertEqual(data, {"department": "経営企画室", "title": "室長"})

    def test_role_only_department_is_cleared_and_missing_title_is_recovered(self):
        cases = [
            ("専務取締役", "専務取締役"),
            ("常務取締役", "常務取締役"),
            ("代表取締役", "代表取締役"),
            ("代表取締役会長", "代表取締役会長"),
            ("専務執行役員", "専務執行役員"),
            ("部長", "部長"),
            ("専務 取締役", "専務取締役"),
            ("エンジニア", "エンジニア"),
            ("リーダー", "リーダー"),
        ]
        for department, role in cases:
            for title in (role, ""):
                with self.subTest(department=department, title=title):
                    output = json.dumps({"department": department, "title": title}, ensure_ascii=False)
                    with patch("src.services.extractor._generate_structured_response", return_value=output):
                        result = extract_card_fields(department, []).data
                    self.assertEqual((result["department"], result["title"]), ("", role))

    def test_role_only_department_does_not_replace_a_distinct_title(self):
        data = {"department": "専務取締役", "title": "専務執行役員"}

        _separate_department_and_title(data, "専務取締役\n専務執行役員")

        self.assertEqual(data, {"department": "", "title": "専務執行役員"})

    def test_role_without_ocr_evidence_is_not_moved_to_title(self):
        output = json.dumps({"department": "専務取締役", "title": ""}, ensure_ascii=False)
        with patch("src.services.extractor._generate_structured_response", return_value=output):
            result = extract_card_fields("営業部", []).data

        self.assertEqual((result["department"], result["title"]), ("", ""))

    def test_department_containing_role_words_is_preserved(self):
        for department in ("取締役会事務局", "専務室", "部長室", "営業部"):
            with self.subTest(department=department):
                data = {"department": department, "title": "専務取締役"}
                _separate_department_and_title(data, f"{department}\n専務取締役")
                self.assertEqual(data, {"department": department, "title": "専務取締役"})

    def test_executive_title_is_separated_from_printed_department(self):
        data = {"department": "経営企画室 専務取締役", "title": "経営企画室 専務取締役"}

        _separate_department_and_title(data, "経営企画室 専務取締役")

        self.assertEqual(data, {"department": "経営企画室", "title": "専務取締役"})

    def test_skipped_middle_department_is_restored_from_printed_line(self):
        ocr = "カスタマー営業部 ビジネスソリューション部 第二課\n大竹 将矢"
        output = json.dumps({"person_name": "大竹 将矢", "department": "営業部 第二課"},
                            ensure_ascii=False)
        with patch("src.services.extractor._generate_structured_response", return_value=output):
            result = extract_card_fields(ocr, []).data
        self.assertEqual(result["department"], "カスタマー営業部 ビジネスソリューション部 第二課")

    def test_tel_without_separator_is_not_taken_from_mobile_label(self):
        ocr = "TEL03-6225-3009 FAX03-6225-3001\n携帯電話:080-4638-9063"
        output = json.dumps({"tel": "03-6225-3009", "mobile": "080-4638-9063",
                             "fax": "03-6225-3001"}, ensure_ascii=False)
        with patch("src.services.extractor._generate_structured_response", return_value=output):
            result = extract_card_fields(ocr, []).data
        self.assertEqual(result["tel"], "03-6225-3009")
        self.assertEqual(result["mobile"], "080-4638-9063")
        self.assertEqual(result["fax"], "03-6225-3001")

    def test_printed_role_is_recovered_when_combined_title_was_rejected(self):
        data = {"department": "研究開発部 部長", "title": ""}

        _separate_department_and_title(data, "研究開発部 部長\n朝日 智子")

        self.assertEqual(data, {"department": "研究開発部", "title": "部長"})

    def test_correct_separation_is_preserved(self):
        data = {"department": "企画開発部", "title": "部長"}

        _separate_department_and_title(data, "企画開発部 部長\n青葉 花子")

        self.assertEqual(data, {"department": "企画開発部", "title": "部長"})

    def test_unrelated_department_and_title_are_not_overwritten(self):
        data = {"department": "開発部", "title": "研究員"}

        _separate_department_and_title(data, "営業部 課長\n開発部\n研究員")

        self.assertEqual(data, {"department": "開発部", "title": "研究員"})

    def test_scanned_demo_department_and_title_regressions(self):
        # OCR line and model fields were captured from the 20 scanned demo cards
        # plus the earlier reference card.  Run the full extraction postprocess.
        cases = [
            ("春風", "総務部 係長", "総務部 係長", "総務部係長", "総務部", "係長"),
            ("日向", "編集部 編集者", "編集部 編集者", "編集者", "編集部", "編集者"),
            ("水辺", "調査部 研究員", "調査部 研究員", "調査研究員", "調査部", "研究員"),
            ("木森", "営業企画部 主任", "営業企画部 主任", "営業企画部主任", "営業企画部", "主任"),
            ("遠山", "経営企画室 室長", "経営企画室", "経営企画室 室長", "経営企画室", "室長"),
            ("北斗", "設計部 技師", "設計部 技師", "技師", "設計部", "技師"),
            ("茜", "業務部 課長", "業務部 課長", "業務部課長", "業務部", "課長"),
            ("森", "商品開発部 主任", "商品開発部 主任", "主任", "商品開発部", "主任"),
            ("銀河", "情報システム部 部長", "情報システム部 部長", "情報システム部長", "情報システム部", "部長"),
            ("月見", "技術部 リーダー", "技術部 リーダー", "技術部長", "技術部", "リーダー"),
            ("花咲", "広報部 担当", "広報部", "広報担当", "広報部", "担当"),
            ("楓", "生産技術部 課長", "生産技術部 課長", "生産技術部課長", "生産技術部", "課長"),
            ("白樺", "顧客支援部 主任", "顧客支援部 主任", "主任", "顧客支援部", "主任"),
            ("虹野", "制作部 部長", "制作部 部長", "制作部長", "制作部", "部長"),
            ("桜川", "営業部 課長", "営業部 課長", "営業部課長", "営業部", "課長"),
            ("若葉", "企画部 主任", "企画部 主任", "企画部主任", "企画部", "主任"),
            ("青空", "開発部 エンジニア", "開発部 エンジニア", "技術担当者", "開発部", "エンジニア"),
            ("星野", "営業一課 係長", "営業一課 係長", "営業一課 係長", "営業一課", "係長"),
            ("湊", "制作部 デザイナー", "制作部", "デザイナー", "制作部", "デザイナー"),
            ("朝日", "研究開発部 部長", "研究開発部 部長", "研究開発部長", "研究開発部", "部長"),
            ("青葉", "企画開発部 部長", "企画開発部", "部長", "企画開発部", "部長"),
        ]
        for label, ocr_line, model_department, model_title, department, title in cases:
            with self.subTest(card=label):
                model_output = json.dumps({"department": model_department, "title": model_title}, ensure_ascii=False)
                with patch("src.services.extractor._generate_structured_response", return_value=model_output):
                    result = extract_card_fields(ocr_line, []).data
                self.assertEqual((result["department"], result["title"]), (department, title))

    def test_scanned_demo_identity_regressions(self):
        # These OCR lines and bad model fields came from the scanned demo cards.
        # Only fields visible in the OCR are included in this regression corpus.
        cases = [
            ("木森", "こもれびサービス株式会社", "木森 拓也", "TAKUYA KIMORI", "takuya.kimori@example.com",
             "こもれびサービス株式会社", "こもれび サービス", "こもれびサービス株式会社", "木森 拓也", "きもり たくや", "こもれびサービス株式会社"),
            ("茜", "あかね物流株式会社", "茜 真由", "MAYU AKANE", "mayu.akane@example.com",
             "あかね 物流 株式会社", "あかね ろぐりてすと", "あかね物流株式会社", "茜 真由", "あかね まゆ", "あかね物流株式会社"),
            ("楓", "楓工業株式会社", "楓 大輔", "DAISUKE KAEDE", "daisuke.kaede@example.com",
             "楓 工業株式会社", "楓 大輔", "楓工業株式会社", "楓 大輔", "かえで だいすけ", "楓工業株式会社"),
            ("湊", "みなとデザイン株式会社", "湊 翔太", "SHOTA MINATO", "shota.minato@example.com",
             "みなと デザイン 株式会社", "みなと デザイン 株式会社", "MINATO DESIGN", "湊 翔太", "みなと しょうた", "みなとデザイン株式会社"),
            ("北斗", "株式会社北斗エンジニアリング", "北斗 誠", "MAKOTO HOKUTO", "makoto.hokuto@example.com",
             "北斗 誠", "北斗 せい", "ホクトエンジニアリング", "北斗 誠", "ほくと まこと", "株式会社北斗エンジニアリング"),
            ("花咲", "株式会社花咲プランニング", "花咲 彩香", "AYAKA HANASAKI", "ayaka.hanasaki@example.com",
             "花咲 彩香", "はなこ あおば", "株式会社花咲プランニング", "花咲 彩香", "はなさき あやか", "株式会社花咲プランニング"),
            ("朝日", "株式会社朝日ラボ", "朝日 智子", "TOMOKO ASAHI", "tomoko.asahi@example.com",
             "朝日 智子", "あおば はなこ", "株式会社朝日ラボ", "朝日 智子", "あさひ ともこ", "株式会社朝日ラボ"),
            ("星野", "株式会社星野商事", "星野 由美", "YUMI HOSHINO", "yumi.hoshino@example.com",
             "星野 由美", "あおば はなこ", "株式会社星野商事", "星野 由美", "ほしの ゆみ", "株式会社星野商事"),
        ]
        for (label, company_ocr, name_ocr, roman_ocr, email_ocr,
             model_name, model_kana, model_company, name, kana, company) in cases:
            with self.subTest(card=label):
                ocr = "\n".join((company_ocr, name_ocr, roman_ocr, email_ocr))
                model_output = json.dumps({
                    "person_name": model_name,
                    "person_name_kana": model_kana,
                    "company_name": model_company,
                }, ensure_ascii=False)
                with patch("src.services.extractor._generate_structured_response", return_value=model_output):
                    result = extract_card_fields(ocr, []).data
                self.assertEqual(
                    (result["person_name"], result["person_name_kana"], result["company_name"]),
                    (name, kana, _normalize_company_name(company)),
                )

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
