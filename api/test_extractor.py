import json
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


from synthetic_fixtures import _generated_identity

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
                data = {"person_name": "風 丘 奏太"}
                blocks = [
                    {"text": "風 丘", "box": [100, 100, 220, 160], "_side": "front"},
                    {"text": "奏太", "box": [250, y, 370, y + 60], "_side": side},
                ]
                _join_spaced_person_name(data, "風 丘\n奏太", blocks)
                self.assertEqual(data["person_name"], "風丘奏太")

    def test_joins_name_blocks_by_horizontal_position(self):
        blocks = [
            {"text": "空", "box": [470, 376, 629, 478], "_side": "front"},
            {"text": "森", "box": [633, 393, 750, 471], "_side": "front"},
            {"text": "律 明", "box": [757, 377, 1139, 483], "_side": "front"},
        ]

        source = _with_spatial_name_candidates("橋\n律 明\n口", blocks)

        self.assertIn("- 空森律明", source)
        extracted = {"person_name": "空森 律明"}
        _remove_ungrounded_values(extracted, source)
        self.assertEqual(extracted["person_name"], "空森 律明")

    def test_does_not_join_blocks_from_opposite_sides(self):
        blocks = [
            {"text": "空森", "box": [100, 100, 220, 180], "_side": "front"},
            {"text": "律明", "box": [225, 100, 345, 180], "_side": "back"},
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

        _recover_printed_identity(data, "こもれびサービス株式会社\n木林 拓也\nTAKUYA KIMORI\ninfo@example.com")

        self.assertEqual(data["person_name"], "木林 拓也")
        self.assertEqual(data["company_name"], "こもれびサービス株式会社")
        _refine_person_name_kana(data, "木林 拓也\nTAKUYA KIMORI\ninfo@example.com")
        self.assertEqual(data["person_name_kana"], "")

    def test_split_name_with_ruby_replaces_a_company_in_person_field(self):
        ocr = "株式会社サンプル研究\nおか\nや\nほし\nはる\n晴 矢\n星 丘\nharuya_hoshioka@example.test"
        blocks = [
            {"text": "ほし", "box": [836, 739, 942, 795], "_side": "front"},
            {"text": "おか", "box": [1142, 737, 1248, 795], "_side": "front"},
            {"text": "はる", "box": [1523, 741, 1626, 791], "_side": "front"},
            {"text": "や", "box": [1848, 737, 1906, 786], "_side": "front"},
            {"text": "晴 矢", "box": [1492, 777, 1955, 933], "_side": "front"},
            {"text": "星 丘", "box": [812, 786, 1269, 933], "_side": "front"},
        ]
        output = json.dumps({"person_name": "株式会社 サンプル研究",
                             "person_name_kana": "サンプル研究 ジェイピーエックス",
                             "company_name": "株式会社 サンプル研究"}, ensure_ascii=False)
        with patch("src.services.extractor._generate_structured_response", return_value=output):
            result = extract_card_fields(ocr, blocks).data

        self.assertEqual(result["person_name"], "星丘 晴矢")
        self.assertEqual(result["person_name_kana"], "ほしおか はるや")
        self.assertEqual(result["company_name"], "株式会社 サンプル研究")
        self.assertEqual(result["_raw"]["person_name"], "株式会社 サンプル研究")

    def test_large_four_kanji_name_replaces_latin_logo_in_person_field(self):
        ocr = "SAMPLE\n平塚営業所 秦野出張所 営業担当\nL&F\n月 野 祥 介\n物流システム\n試験L&F工業株式会社\ntsukino@example.test"
        blocks = [
            {"text": "SAMPLE", "box": [807, 49, 1021, 99], "font_size": 50, "_side": "front"},
            {"text": "月 野 祥 介", "box": [126, 198, 527, 261], "font_size": 63, "_side": "front"},
            {"text": "試験L&F工業株式会社", "box": [125, 324, 714, 375], "font_size": 51, "_side": "front"},
        ]
        output = json.dumps({"person_name": "SAMPLE", "person_name_kana": "トヨタ",
                             "company_name": "L&F"}, ensure_ascii=False)
        response = SimpleNamespace(raise_for_status=lambda: None, json=lambda: {
            "candidates": [{"reading": "ツキノヒロアキ"}], "family_candidates": [{"reading": "ツキノ"}]})
        with patch("src.services.extractor._generate_structured_response", return_value=output), \
             patch("src.services.kana_reading.settings", SimpleNamespace(kana_base_url="http://kana:8001")), \
             patch("src.services.kana_reading.requests.post", return_value=response):
            result = extract_card_fields(ocr, blocks).data

        self.assertEqual(result["person_name"], "月野 祥介")
        self.assertEqual(result["company_name"], "試験L&F工業 株式会社")
        self.assertEqual(result["_raw"]["person_name"], "SAMPLE")

    def test_rescan_retains_supported_reading_for_same_person(self):
        ocr = "SAMPLE\n月 野 祥 介\n試験L&F工業株式会社\ntsukino@example.test"
        blocks = [{"text": "月 野 祥 介", "box": [126, 198, 527, 261],
                   "font_size": 63, "_side": "front"}]
        output = '{"person_name":"SAMPLE","person_name_kana":"トヨタ","email":"tsukino@example.test"}'
        previous = {"person_name": "月野 祥介", "person_name_kana": "つきの しょうすけ",
                    "email": "tsukino@example.test", "mobile": ""}

        response = SimpleNamespace(raise_for_status=lambda: None, json=lambda: {
            "candidates": [{"reading": "ツキノヒロアキ"}, {"reading": "ツキノショウスケ"}],
            "family_candidates": [{"reading": "ツキノ"}]})
        with patch("src.services.extractor._generate_structured_response", return_value=output), \
             patch("src.services.kana_reading.settings", SimpleNamespace(kana_base_url="http://kana:8001")), \
             patch("src.services.kana_reading.requests.post", return_value=response):
            result = extract_card_fields(ocr, blocks, previous=previous).data
            unrelated = extract_card_fields(ocr, blocks, previous={**previous, "email": "other@example.test"}).data

        self.assertEqual(result["person_name"], "月野 祥介")
        self.assertEqual(result["person_name_kana"], "つきの しょうすけ")
        self.assertTrue(result["_model"]["kana"]["previous_reading_retained"])
        self.assertEqual(unrelated["person_name_kana"], "つきの ひろあき")

    def test_parenthesized_area_code_keeps_printed_tel_and_fax(self):
        data = {"tel": "0465-00-0101", "fax": "0465-00-0102"}

        _remove_ungrounded_values(data, "TEL(0465)-00-0101 FAX(0465)-00-0102")

        self.assertEqual(data["tel"], "0465-00-0101")
        self.assertEqual(data["fax"], "0465-00-0102")

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

        _separate_department_and_title(data, "経営企画室 室長\n霧丘 灯里")

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

    def test_standalone_group_leader_recovers_a_rejected_invented_title(self):
        source = "星路 奏文\nメカトロニクス部\n機構設計グループ\nグループ長"
        for title in ("", "部長", "Manager"):
            output = {"department": "機構設計グループ", "title": title}
            with self.subTest(title=title), \
                 patch("src.services.extractor._generate_structured_response", return_value=json.dumps(output)):
                result = extract_card_fields(source, []).data
            self.assertEqual(result["title"], "グループ長")
            self.assertEqual(result["department"], "機構設計グループ")
            self.assertEqual(result["_raw"], output)
            self.assertEqual(result["_automatic"]["title"], "グループ長")

    def test_standalone_role_recovery_handles_duplicates_and_character_spacing(self):
        for source, title in (("研究開発部\n主任", "主任"),
                              ("グループ 長\nグループ長", "グループ長"),
                              ("【表面】\n課長\n【裏面】\n課長", "課長")):
            with self.subTest(source=source), \
                 patch("src.services.extractor._generate_structured_response", return_value='{}'):
                result = extract_card_fields(source, []).data
            self.assertEqual(result["title"], title)

    def test_role_recovery_does_not_choose_between_distinct_printed_roles(self):
        with patch("src.services.extractor._generate_structured_response", return_value='{}'):
            result = extract_card_fields("開発部\n部長\nグループ長", []).data
        self.assertEqual(result["title"], "")

    def test_role_recovery_preserves_an_existing_grounded_title(self):
        with patch("src.services.extractor._generate_structured_response", return_value='{"title":"課長"}'):
            result = extract_card_fields("開発部\n課長\nグループ長", []).data
        self.assertEqual(result["title"], "課長")

    def test_role_recovery_requires_a_standalone_ocr_row(self):
        for source in ("グループ長室", "グループ長補佐室", "部長室", "機構設計グループ", "株式会社グループ長"):
            with self.subTest(source=source), \
                 patch("src.services.extractor._generate_structured_response", return_value='{}'):
                result = extract_card_fields(source, []).data
            self.assertEqual(result["title"], "")

    def test_group_leader_is_separated_from_a_department(self):
        for source in ("機構設計グループ グループ長", "機構設計グループ\nグループ長"):
            with self.subTest(source=source), \
                 patch("src.services.extractor._generate_structured_response",
                       return_value='{"department":"機構設計グループ","title":""}'):
                result = extract_card_fields(source, []).data
            self.assertEqual((result["department"], result["title"]), ("機構設計グループ", "グループ長"))

    def test_group_leader_in_department_is_moved_to_title(self):
        with patch("src.services.extractor._generate_structured_response", return_value='{"department":"グループ長"}'):
            result = extract_card_fields("グループ長", []).data
        self.assertEqual((result["department"], result["title"]), ("", "グループ長"))

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
        ocr = "カスタマー営業部 ビジネスソリューション部 第二課\n星丘 晴矢"
        output = json.dumps({"person_name": "星丘 晴矢", "department": "営業部 第二課"},
                            ensure_ascii=False)
        with patch("src.services.extractor._generate_structured_response", return_value=output):
            result = extract_card_fields(ocr, []).data
        self.assertEqual(result["department"], "カスタマー営業部 ビジネスソリューション部 第二課")

    def test_tel_without_separator_is_not_taken_from_mobile_label(self):
        ocr = "TEL03-0000-0108 FAX03-0000-0109\n携帯電話:080-0000-0110"
        output = json.dumps({"tel": "03-0000-0108", "mobile": "080-0000-0110",
                             "fax": "03-0000-0109"}, ensure_ascii=False)
        with patch("src.services.extractor._generate_structured_response", return_value=output):
            result = extract_card_fields(ocr, []).data
        self.assertEqual(result["tel"], "03-0000-0108")
        self.assertEqual(result["mobile"], "080-0000-0110")
        self.assertEqual(result["fax"], "03-0000-0109")

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

    def test_department_and_title_structure_regressions(self):
        cases = [
            ('総務部 係長', '総務部 係長', '総務部係長', '総務部', '係長'),
            ('編集部 編集者', '編集部 編集者', '編集者', '編集部', '編集者'),
            ('調査部 研究員', '調査部 研究員', '調査研究員', '調査部', '研究員'),
            ('営業企画部 主任', '営業企画部 主任', '営業企画部主任', '営業企画部', '主任'),
            ('経営企画室 室長', '経営企画室', '経営企画室 室長', '経営企画室', '室長'),
            ('設計部 技師', '設計部 技師', '技師', '設計部', '技師'),
            ('業務部 課長', '業務部 課長', '業務部課長', '業務部', '課長'),
            ('商品開発部 主任', '商品開発部 主任', '主任', '商品開発部', '主任'),
            ('情報システム部 部長', '情報システム部 部長', '情報システム部長', '情報システム部', '部長'),
            ('技術部 リーダー', '技術部 リーダー', '技術部長', '技術部', 'リーダー'),
            ('広報部 担当', '広報部', '広報担当', '広報部', '担当'),
            ('生産技術部 課長', '生産技術部 課長', '生産技術部課長', '生産技術部', '課長'),
            ('顧客支援部 主任', '顧客支援部 主任', '主任', '顧客支援部', '主任'),
            ('制作部 部長', '制作部 部長', '制作部長', '制作部', '部長'),
            ('営業部 課長', '営業部 課長', '営業部課長', '営業部', '課長'),
            ('企画部 主任', '企画部 主任', '企画部主任', '企画部', '主任'),
            ('開発部 エンジニア', '開発部 エンジニア', '技術担当者', '開発部', 'エンジニア'),
            ('営業一課 係長', '営業一課 係長', '営業一課 係長', '営業一課', '係長'),
            ('制作部 デザイナー', '制作部', 'デザイナー', '制作部', 'デザイナー'),
            ('研究開発部 部長', '研究開発部 部長', '研究開発部長', '研究開発部', '部長'),
            ('企画開発部 部長', '企画開発部', '部長', '企画開発部', '部長'),
        ]
        for index, (ocr_line, model_department, model_title, department, title) in enumerate(cases):
            with self.subTest(case=index):
                model_output = json.dumps({"department": model_department, "title": model_title}, ensure_ascii=False)
                with patch("src.services.extractor._generate_structured_response", return_value=model_output):
                    result = extract_card_fields(ocr_line, []).data
                self.assertEqual((result["department"], result["title"]), (department, title))

    def test_generated_identities_recover_from_company_and_reading_confusion(self):
        for seed in range(8):
            person = _generated_identity(seed + 400)
            company = f"株式会社 {person['company']}"
            for proposed in (company, person['name']):
                with self.subTest(seed=seed, proposed=proposed):
                    ocr = "\n".join((company, person['name'], person['roman'], person['email']))
                    output = json.dumps({"person_name": proposed, "person_name_kana": "まちがい よみ",
                                         "company_name": company}, ensure_ascii=False)
                    family, given = person['kana'].split()
                    prediction = SimpleNamespace(raise_for_status=lambda: None, json=lambda: {
                        "model": "synthetic", "candidates": [{"reading": family + given}],
                        "family_candidates": [{"reading": family}],
                    })
                    with patch("src.services.extractor._generate_structured_response", return_value=output), \
                         patch("src.services.kana_reading.settings", SimpleNamespace(kana_base_url="http://kana:8001")), \
                         patch("src.services.kana_reading.requests.post", return_value=prediction):
                        result = extract_card_fields(ocr, []).data
                    self.assertEqual((result['person_name'], result['person_name_kana'], result['company_name']),
                                     (person['name'], person['kana'], _normalize_company_name(company)))

    def test_ruby_blocks_near_the_name_override_a_kana_guess(self):
        data = {"person_name": "青葉 花子", "person_name_kana": "あおば はこ"}
        blocks = [
            {"text": "アオバ", "box": [100, 50, 180, 70]},
            {"text": "ハナコ", "box": [190, 50, 270, 70]},
            {"text": "青葉 花子", "box": [100, 90, 270, 140]},
        ]

        _refine_person_name_kana(data, "青葉 花子", blocks)

        self.assertEqual(data["person_name_kana"], "あおば はなこ")

    def test_natsumori_name_order_and_company_punctuation(self):
        blocks = [
            {"text": "綾", "box": [1106, 591, 1367, 694], "_side": "front"},
            {"text": "夏森", "box": [861, 596, 1126, 697], "_side": "front"},
        ]
        data = {
            "person_name": "綾 夏森",
            "person_name_kana": "れつ なつ上",
            "company_name": "テ・ス・ト株式会社",
        }
        raw = "綾\n夏森\nテ·ス·ト株式会社\nRYO NATSUMORI\nr-natsumori@example.test"
        _correct_person_name_order(data, blocks)
        _refine_person_name_kana(data, raw, blocks)
        _remove_ungrounded_values(data, _with_spatial_name_candidates(raw, blocks))
        self.assertEqual(data["person_name"], "夏森 綾")
        self.assertEqual(data["person_name_kana"], "なつもり りょう")
        self.assertEqual(data["company_name"], "テ・ス・ト株式会社")

    def test_multiple_numbers_on_one_line_remain_individual_evidence(self):
        raw = "TEL 0463-00-0105 FAX 0463-00-0106 携帯 080-0000-0107"
        data = {"tel": "0463-00-0105", "fax": "0463-00-0106", "mobile": "080-0000-0107"}
        _remove_ungrounded_values(data, raw)
        self.assertEqual({key: data[key] for key in ("tel", "fax", "mobile")}, {"tel": "0463-00-0105", "fax": "0463-00-0106", "mobile": "080-0000-0107"})

    def test_english_side_phone_labels_correct_swapped_numbers(self):
        raw = "045-000-0104\nTel.+81-45-000-0103 Fax.+81-45-000-0104"
        data = {"tel": "045-000-0104", "fax": "045-000-0104"}
        _prefer_labeled_phone_numbers(data, raw)
        _remove_ungrounded_values(data, raw)
        self.assertEqual(data["tel"], "045-000-0103")
        self.assertEqual(data["fax"], "045-000-0104")


if __name__ == "__main__":
    unittest.main()
