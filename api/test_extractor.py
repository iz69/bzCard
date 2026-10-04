import json
import random
import unittest
from src.services.normalization import _normalize_company_name
from unittest.mock import patch

from src.services.extractor import (
    _correct_person_name_order,
    _prefer_labeled_phone_numbers,
    _recover_printed_identity,
    _refine_person_name_kana,
    _remove_ungrounded_values,
    _roman_to_hiragana,
    _separate_department_and_title,
    _with_spatial_name_candidates,
    extract_card_fields,
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

    def test_store_name_is_replaced_using_printed_name_and_email_initial(self):
        case = _generated_identity(1)
        ocr = "\n".join((case["store"], "営業担当", case["name"], case["roman"],
                         case["company"], case["email"]))
        output = json.dumps({
            "person_name": case["store"], "person_name_kana": case["kana"].split()[0] + " てん",
            "company_name": case["company"], "department": case["store"].replace("店", "部"),
            "title": "営業担当",
        }, ensure_ascii=False)
        with patch("src.services.extractor._generate_structured_response", return_value=output):
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

    def test_mismatched_initial_does_not_recover_a_name(self):
        case = _generated_identity(6)
        data = {"person_name": case["store"]}
        wrong_initial = "z" if case["given_roman"][0] != "Z" else "q"
        email = f"{wrong_initial}.{case['family_roman'].lower()}@example.test"
        _recover_printed_identity(data, "\n".join((case["store"], case["name"],
                                                    case["roman"], email)))
        self.assertEqual(data["person_name"], case["store"])

    def test_ambiguous_printed_people_do_not_replace_a_store(self):
        first, second = _generated_identity(7), _generated_identity(8)
        data = {"person_name": first["store"]}
        ocr = "\n".join((first["store"], first["name"], first["roman"], first["email"],
                         second["name"], second["roman"], second["email"]))
        _recover_printed_identity(data, ocr)
        self.assertEqual(data["person_name"], first["store"])

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
