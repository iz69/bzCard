"""Combined name/reading regressions across OCR spacing, evidence and failures."""
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import requests

from src.services.extractor import extract_card_fields
from src.services.kana_reading import apply_specialist_reading


def model_response(readings, families):
    return SimpleNamespace(raise_for_status=lambda: None, json=lambda: {
        "model": "kanjikana-1.9o",
        "candidates": [{"reading": reading} for reading in readings],
        "family_candidates": [{"reading": family} for family in families],
    })


class PersonIdentityTests(unittest.TestCase):
    def setUp(self):
        self.config = patch("src.services.kana_reading.settings", SimpleNamespace(kana_base_url="http://kana:8001"))
        self.config.start()
        self.addCleanup(self.config.stop)

    def test_iwata_is_recovered_from_small_widely_spaced_name_blocks(self):
        source = "相日防災株式会社\n取締役執行役員\n県央·県東·東京エリア統括兼経営企画室長\n岩 田\n学\niwata@example.test"
        original = [
            {"text": "相日防災株式会社", "box": [286, 61, 621, 97]},
            {"text": "取締役執行役員", "box": [286, 120, 443, 141]},
            {"text": "岩 田", "box": [288, 172, 407, 212]},
            {"text": "学", "box": [537, 174, 576, 208]},
            {"text": "本社認証取得", "box": [34, 350, 140, 367]},
        ]
        raw = json.dumps({"person_name": "相日防災株式会社", "company_name": "相日防災株式会社",
                          "person_name_kana": "あいちじゅうしゃかいていしゃかいていしゃ", "email": "iwata@example.test"})
        for scale in (.25, 1, 4):
            for previous in (None, {"person_name": "岩田 学", "person_name_kana": "いわた まなぶ", "email": "iwata@example.test"}):
                with self.subTest(scale=scale, previous=bool(previous)):
                    blocks = [{**block, "box": [c * scale for c in block["box"]], "_side": "front"}
                              for block in reversed(original)]
                    with patch("src.services.extractor._generate_structured_response", return_value=raw) as generate, \
                         patch("src.services.kana_reading.requests.post", return_value=model_response(["イワタマナブ"], ["イワタ"])):
                        result = extract_card_fields(source, blocks, previous=previous).data
                    self.assertEqual(result["person_name"], "岩田 学")
                    self.assertEqual(result["person_name_kana"], "いわた まなぶ")
                    self.assertEqual(result["company_name"], "相日防災 株式会社")
                    self.assertEqual(result["_raw"]["person_name"], "相日防災株式会社")
                    generate.assert_called_once()

    def test_wide_name_spacing_does_not_join_opposite_sides_rows_or_distant_columns(self):
        for side, box in (("back", [537, 174, 576, 208]),
                          ("front", [537, 274, 576, 308]),
                          ("front", [2000, 174, 2039, 208])):
            with self.subTest(side=side, box=box):
                blocks = [{"text": "岩 田", "box": [288, 172, 407, 212], "_side": "front"},
                          {"text": "学", "box": box, "_side": side}]
                raw = '{"person_name":"相日防災株式会社","company_name":"相日防災株式会社"}'
                with patch("src.services.extractor._generate_structured_response", return_value=raw), \
                     patch("src.services.kana_reading.requests.post") as post:
                    result = extract_card_fields("相日防災株式会社\n岩 田\n学", blocks).data
                self.assertEqual(result["person_name"], "")
                post.assert_not_called()

    def test_printed_two_part_name_in_one_block_is_recovered_without_previous_values(self):
        source = "株式会社ハイ·テック\n青山 恵美\n代表取締役\nEmi\nAoyama\nhitec0465@example.test"
        blocks = [{"text": "青山 恵美", "box": [210, 128, 468, 168], "_side": "front"},
                  {"text": "代表取締役", "box": [56, 140, 194, 159], "_side": "front"}]
        raw = '{"person_name":"株式会社ハイテック","person_name_kana":"ハイテック 株式会社"}'
        with patch("src.services.extractor._generate_structured_response", return_value=raw), \
             patch("src.services.kana_reading.requests.post", return_value=model_response(["アオヤマエミ"], ["アオヤマ"])):
            result = extract_card_fields(source, blocks).data
        self.assertEqual(result["person_name"], "青山 恵美")
        self.assertEqual(result["person_name_kana"], "あおやま えみ")

    def test_spaced_surname_alone_is_not_recovered_as_a_full_name(self):
        source = "相日防災株式会社\n岩 田"
        blocks = [{"text": "岩 田", "box": [288, 172, 407, 212], "_side": "front"}]
        with patch("src.services.extractor._generate_structured_response", return_value='{"person_name":"相日防災株式会社"}'):
            result = extract_card_fields(source, blocks).data
        self.assertEqual(result["person_name"], "")

    def test_yamamuro_rescan_keeps_previous_identity_when_given_name_is_misread(self):
        source = "松浦建設株式会社\n一\n執行 投 員\n山 室\n営業部長\n携帯:080-1234-5678\nyamamuro@example.test"
        blocks = [{"text": "一", "box": [632, 117, 669, 207], "_side": "front"},
                  {"text": "山 室", "box": [335, 168, 474, 204], "_side": "front"}]
        raw = json.dumps({"person_name": "松浦 建設株式会社", "person_name_kana": "まつうら けんせつ",
                          "email": "yamamuro@example.test", "mobile": "080-1234-5678"})
        for kana in ("", "やまむろ いわお", "やまむろいわお"):
            previous = {"person_name": "山室 巌", "person_name_kana": kana,
                        "email": "yamamuro@example.test", "mobile": "080-1234-5678"}
            with self.subTest(kana=kana), \
                 patch("src.services.extractor._generate_structured_response", return_value=raw), \
                 patch("src.services.kana_reading.requests.post") as post:
                result = extract_card_fields(source, blocks, previous=previous).data
            self.assertEqual(result["person_name"], "山室 巌")
            self.assertEqual(result["person_name_kana"], kana)
            self.assertEqual(result["_raw"]["person_name"], "松浦 建設株式会社")
            self.assertEqual(result["_model"]["identity"]["source"], "previous_identity_partial_ocr")
            self.assertEqual(result["_model"]["kana"]["selected"], kana)
            post.assert_not_called()

    def test_partial_ocr_requires_both_printed_identifiers_and_a_standalone_surname(self):
        source = "松浦建設株式会社\n一\n山 室\n携帯:080-1234-5678\nyamamuro@example.test"
        raw = '{"person_name":"松浦 建設株式会社","email":"yamamuro@example.test","mobile":"080-1234-5678"}'
        previous = {"person_name": "山室 巌", "person_name_kana": "やまむろ いわお",
                    "email": "yamamuro@example.test", "mobile": "080-1234-5678"}
        cases = [
            (source, {**previous, "email": "someone@example.test"}),
            (source, {**previous, "mobile": "090-1234-5678"}),
            (source.replace("携帯:080-1234-5678", ""), previous),
            (source.replace("yamamuro@example.test", ""), previous),
            (source.replace("山 室", "山室防災株式会社"), previous),
            (source, {**previous, "person_name": "松浦 建設株式会社"}),
            (source, None),
        ]
        for evidence, old in cases:
            with self.subTest(evidence=evidence, previous=old), \
                 patch("src.services.extractor._generate_structured_response", return_value=raw):
                result = extract_card_fields(evidence, [], previous=old).data
            self.assertEqual(result["person_name"], "")
            self.assertEqual(result["person_name_kana"], "")

    def test_partial_ocr_does_not_replace_a_different_grounded_person(self):
        source = "松浦建設株式会社\n山室 明\n携帯:080-1234-5678\nyamamuro@example.test"
        raw = '{"person_name":"山室 明","email":"yamamuro@example.test","mobile":"080-1234-5678"}'
        previous = {"person_name": "山室 巌", "person_name_kana": "やまむろ いわお",
                    "email": "yamamuro@example.test", "mobile": "080-1234-5678"}
        with patch("src.services.extractor._generate_structured_response", return_value=raw), \
             patch("src.services.kana_reading.requests.post", return_value=model_response(["ヤマムロアキラ"], ["ヤマムロ"])):
            result = extract_card_fields(source, [], previous=previous).data
        self.assertEqual(result["person_name"], "山室 明")
        self.assertEqual(result["person_name_kana"], "やまむろ あきら")

    def test_company_rejection_with_multiple_people_retries_without_feedback(self):
        blocks = [{"text": text, "box": box, "_side": "front"} for text, box in (
            ("岩田", [100, 100, 170, 140]), ("学", [240, 100, 280, 140]),
            ("矢上", [100, 200, 170, 240]), ("寛", [240, 200, 280, 240]),
        )]
        wrong = '{"person_name":"相日防災株式会社"}'
        correct = '{"person_name":"岩田 学"}'
        with patch("src.services.extractor._generate_structured_response", side_effect=[wrong, correct]) as generate, \
             patch("src.services.kana_reading.requests.post", return_value=model_response(["イワタマナブ"], ["イワタ"])):
            result = extract_card_fields("相日防災株式会社\n岩田\n学\n矢上\n寛", blocks).data
        self.assertEqual(result["person_name"], "岩田 学")
        self.assertEqual(generate.call_count, 2)
        self.assertIn("会社名を氏名に使わず", generate.call_args.args[0])
        self.assertEqual(result["_feedback"]["fallback"]["rejected_response_text"], wrong)

    def test_ambiguous_people_and_split_company_are_not_selected_by_recovery(self):
        cases = [
            [("岩田", [100, 100, 170, 140]), ("学", [240, 100, 280, 140]),
             ("矢上", [100, 200, 170, 240]), ("寛", [240, 200, 280, 240])],
            [("相日防災", [100, 100, 270, 140]), ("株式会社", [300, 100, 470, 140]),
             ("常務", [100, 200, 170, 240]), ("取締役", [210, 200, 310, 240])],
            [("相日 防災", [100, 100, 270, 140]), ("株式会社", [300, 100, 470, 140])],
        ]
        for rows in cases:
            with self.subTest(rows=rows):
                source = "相日防災株式会社\n" + "\n".join(text for text, _box in rows)
                blocks = [{"text": text, "box": box, "_side": "front"} for text, box in rows]
                with patch("src.services.extractor._generate_structured_response", return_value='{"person_name":"相日防災株式会社"}'), \
                     patch("src.services.kana_reading.requests.post") as post:
                    result = extract_card_fields(source, blocks).data
                self.assertEqual(result["person_name"], "")
                post.assert_not_called()

    def test_reported_names_share_the_same_spacing_and_reading_pipeline(self):
        cases = [
            ("浦", "伸一", "うら", "しんいち"),
            ("佐藤", "敦俊", "さとう", "あつとし"),
            ("芳賀", "大樹", "はが", "だいじゅ"),
            ("大竹", "将矢", "おおたけ", "まさや"),
            ("三木", "洋亮", "みき", "ようすけ"),
            ("鶴川", "達也", "つるかわ", "たつや"),
            ("秦", "嘉一郎", "はた", "かいちろう"),
            ("長谷川", "亮", "はせがわ", "りょう"),
        ]
        for family, given, family_kana, given_kana in cases:
            canonical = f"{family} {given}"
            kana = f"{family_kana} {given_kana}"
            blocks = [
                {"text": " ".join(given), "box": [270, 100, 440, 170], "_side": "front"},
                {"text": " ".join(family), "box": [100, 100, 240, 170], "_side": "front"},
            ]
            source = f"株式会社 例\n{' '.join(given)}\n{' '.join(family)}"
            variants = {canonical, family + given, " ".join(family + given),
                        f"{' '.join(family)} {given}", f"{family} {' '.join(given)}",
                        f"{given} {family}"}
            for proposed in variants:
                with self.subTest(name=canonical, proposed=proposed):
                    raw = json.dumps({"person_name": proposed, "person_name_kana": kana}, ensure_ascii=False)
                    response = model_response([family_kana + given_kana], [family_kana])
                    with patch("src.services.extractor._generate_structured_response", return_value=raw), \
                         patch("src.services.kana_reading.requests.post", return_value=response):
                        result = extract_card_fields(source, blocks).data
                    self.assertEqual(result["person_name"], canonical)
                    self.assertEqual(result["person_name_kana"], kana)
                    self.assertEqual(result["_raw"]["person_name"], proposed)
                    self.assertEqual(result["_automatic"]["person_name"], canonical)
                    self.assertIn("identity", result["_model"])

    def test_four_separate_kanji_do_not_force_a_two_character_surname(self):
        def predict(_url, *, json, timeout):
            return model_response(["ハタカイチロウ"], ["ハタ" if json["family"] == "秦" else "チガウ"])

        source = "秦 嘉 一 郎\nKaichiro Hata\nkaichiro.hata@example.test"
        raw = '{"person_name":"秦 嘉 一 郎","person_name_kana":""}'
        with patch("src.services.extractor._generate_structured_response", return_value=raw), \
             patch("src.services.kana_reading.requests.post", side_effect=predict):
            result = extract_card_fields(source, []).data
        self.assertEqual(result["person_name"], "秦 嘉一郎")
        self.assertEqual(result["person_name_kana"], "はた かいちろう")

    def test_ocr_fragment_containing_part_of_given_name_does_not_change_boundary(self):
        source = "佐藤 正\n宗\nsato.masamune@example.test"
        blocks = [
            {"text": "佐藤 正", "box": [579, 463, 1178, 582], "_side": "front"},
            {"text": "宗", "box": [1187, 468, 1402, 577], "_side": "front"},
        ]
        raw = '{"person_name":"佐藤 正宗","person_name_kana":"さとう まさむね"}'
        response = model_response(["サトウマサムネ"], ["サトウ", "サト"])
        with patch("src.services.extractor._generate_structured_response", return_value=raw), \
             patch("src.services.kana_reading.requests.post", return_value=response):
            result = extract_card_fields(source, blocks).data
        self.assertEqual(result["person_name"], "佐藤 正宗")
        self.assertEqual(result["person_name_kana"], "さとう まさむね")

    def test_company_response_is_recovered_from_name_row_with_a_partial_given_name_block(self):
        source = "株式会社 例\n佐藤 正\n宗\nsato.masamune@example.test"
        blocks = [{"text": "佐藤 正", "box": [579, 463, 1178, 582], "_side": "front"},
                  {"text": "宗", "box": [1187, 468, 1402, 577], "_side": "front"}]
        def predict(_url, *, json, timeout):
            return model_response(["サトウマサムネ"], ["サトウ" if json["family"] == "佐藤" else "チガウ"])
        with patch("src.services.extractor._generate_structured_response", return_value='{"person_name":"株式会社 例"}'), \
             patch("src.services.kana_reading.requests.post", side_effect=predict):
            result = extract_card_fields(source, blocks).data
        self.assertEqual(result["person_name"], "佐藤 正宗")
        self.assertEqual(result["person_name_kana"], "さとう まさむね")

    def test_model_family_alternatives_are_not_conflicting_printed_evidence(self):
        data = {"person_name": "佐藤 敦俊", "person_name_kana": ""}
        response = model_response(["サトウアツトシ"], ["サトウ", "サト", "サフジ"])
        with patch("src.services.kana_reading.requests.post", return_value=response):
            info = apply_specialist_reading(data, "佐藤 敦俊\natsutoshi.sato@example.test", [])
        self.assertEqual(data["person_name_kana"], "さとう あつとし")
        self.assertNotEqual(info["status"], "uncertain")

    def test_family_first_roman_requires_order_evidence_when_model_is_down(self):
        source = "株式会社 例\n佐藤 敦俊\nsato.atsutoshi@example.test"
        raw = '{"person_name":"株式会社 例","person_name_kana":"かぶしき かいしゃ","email":"sato.atsutoshi@example.test"}'
        old = {"person_name": "佐藤 敦俊", "person_name_kana": "さとう あつとし",
               "email": "sato.atsutoshi@example.test"}
        for previous, expected in ((None, ""), (old, "さとう あつとし")):
            with self.subTest(previous=bool(previous)), \
                 patch("src.services.extractor._generate_structured_response", return_value=raw), \
                 patch("src.services.kana_reading.requests.post", side_effect=requests.ConnectionError()):
                result = extract_card_fields(source, [], previous=previous).data
            self.assertEqual(result["person_name"], "佐藤 敦俊")
            self.assertEqual(result["person_name_kana"], expected)

    def test_existing_reading_survives_model_outage_or_empty_candidates_only_for_same_person(self):
        source = "TOYOTA\n三木 洋亮\nmiki@example.test"
        raw = '{"person_name":"TOYOTA","person_name_kana":"とよた かぶしき","email":"miki@example.test"}'
        old = {"person_name": "三木 洋亮", "person_name_kana": "みき ようすけ", "email": "miki@example.test"}
        for model in (requests.ConnectionError(), model_response([], [])):
            for previous, expected in ((old, "みき ようすけ"), ({**old, "email": "other@example.test"}, "")):
                with self.subTest(model=type(model).__name__, same=previous == old), \
                     patch("src.services.extractor._generate_structured_response", return_value=raw), \
                     patch("src.services.kana_reading.requests.post", side_effect=model if isinstance(model, Exception) else None,
                           return_value=model):
                    result = extract_card_fields(source, [], previous=previous).data
                self.assertEqual(result["person_name_kana"], expected)
                self.assertEqual(result["_model"]["kana"]["selected"], expected)

    def test_printed_reading_overrides_previous_guess(self):
        old = {"person_name": "三木 洋亮", "person_name_kana": "みき ひろあき", "email": "miki@example.test"}
        data = {**old, "person_name_kana": ""}
        with patch("src.services.kana_reading.requests.post") as post:
            info = apply_specialist_reading(data, "三木 洋亮（みき ようすけ）\nmiki@example.test", [], previous=old)
        self.assertEqual(data["person_name_kana"], "みき ようすけ")
        self.assertEqual(info["status"], "printed")
        post.assert_not_called()

    def test_rescan_does_not_use_a_new_reversed_llm_reading_as_roman_order_evidence(self):
        old = {"person_name": "佐藤 敦俊", "person_name_kana": "さとう あつとし", "email": "sato.atsutoshi@example.test"}
        data = {**old, "person_name_kana": "あつとし さと"}
        with patch("src.services.kana_reading.requests.post", side_effect=requests.ConnectionError()):
            info = apply_specialist_reading(data, "佐藤 敦俊\nsato.atsutoshi@example.test", [], previous=old)
        self.assertEqual(data["person_name_kana"], "さとう あつとし")
        self.assertEqual(info["selected"], data["person_name_kana"])

    def test_roman_reading_corrects_a_rare_surname_using_supported_given_name_order(self):
        for roman, email in (("TSUKUDA YOSHINE", "y.tsukuda"), ("YOSHINE TSUKUDA", "tsukuda.yoshine")):
            with self.subTest(roman=roman), \
                 patch("src.services.kana_reading.requests.post", return_value=model_response(["ツギタヨシネ"], ["ツギタ"])):
                data = {"person_name": "次田 芳音", "person_name_kana": ""}
                info = apply_specialist_reading(data, f"次田 芳音\n{roman}\n{email}@example.test", [])
            self.assertEqual(data["person_name_kana"], "つくだ よしね")
            self.assertEqual(info["source"], "roman")

    def test_qualification_and_role_lines_are_never_recovered_as_people(self):
        for label in ("二級建築士", "技術部 リーダー", "機械組付課 係長", "相日 防災"):
            blocks = [{"text": "相日 防災", "box": [100, 100, 250, 140]},
                      {"text": "株式会社", "box": [280, 100, 380, 140]}] if label == "相日 防災" else []
            with self.subTest(label=label), \
                 patch("src.services.extractor._generate_structured_response", return_value='{"person_name":"株式会社 例"}'):
                data = extract_card_fields(f"株式会社 例\n{label}", blocks).data
            self.assertEqual(data["person_name"], "")

    def test_extended_kanji_are_retained_and_recovered_without_character_substitution(self):
        for name in ("﨑田 健一", "𠮷田 健一", "髙橋 健一"):
            raw = json.dumps({"person_name": "株式会社 例"})
            with self.subTest(name=name), \
                 patch("src.services.extractor._generate_structured_response", return_value=raw), \
                 patch("src.services.kana_reading.requests.post", side_effect=requests.ConnectionError()):
                data = extract_card_fields(f"株式会社 例\n{name}", []).data
            self.assertEqual(data["person_name"], name)

    def test_ruby_reading_is_independent_of_image_scale(self):
        blocks = [
            {"text": "大竹", "box": [100, 100, 220, 160]},
            {"text": "将矢", "box": [250, 100, 370, 160]},
            {"text": "おおたけ", "box": [100, 75, 220, 95]},
            {"text": "まさや", "box": [250, 75, 370, 95]},
        ]
        for scale in (.1, 1, 4):
            with self.subTest(scale=scale), \
                 patch("src.services.extractor._generate_structured_response", return_value='{"person_name":"株式会社 例"}'), \
                 patch("src.services.kana_reading.requests.post") as post:
                scaled = [{**block, "box": [c * scale for c in block["box"]]} for block in blocks]
                data = extract_card_fields("株式会社 例\n大竹\n将矢\nおおたけ\nまさや", scaled).data
            self.assertEqual((data["person_name"], data["person_name_kana"]), ("大竹 将矢", "おおたけ まさや"))
            post.assert_not_called()
    def test_roman_reading_rejects_wrong_ocr_surname_boundary(self):
        def predict(_url, *, json, timeout):
            family = "サトウ" if json["family"] == "佐藤" else "サトウマサ"
            return model_response(["サトウマサムネ"], [family])

        data = {"person_name": "佐藤正 宗", "person_name_kana": "さとう まさむね"}
        with patch("src.services.kana_reading.requests.post", side_effect=predict):
            info = apply_specialist_reading(data, "佐藤 正\n宗\nsato.masamune@example.test", [])
        self.assertEqual(data["person_name"], "佐藤 正宗")
        self.assertEqual(data["person_name_kana"], "さとう まさむね")
        self.assertEqual(info["segmentation"], "verified")

    def test_unrelated_english_words_do_not_block_specialist_reading(self):
        data = {"person_name": "矢上 寛", "person_name_kana": "やがみ わか"}
        with patch("src.services.kana_reading.requests.post", return_value=model_response(["ヤガミヒロシ"], ["やがみ"])):
            info = apply_specialist_reading(data, "矢上 寛\nBUREAU VERITAS\ninfo@example.test", [])
        self.assertEqual(data["person_name_kana"], "やがみ ひろし")
        self.assertEqual(info["source"], "specialist")

    def test_conflicting_printed_readings_are_reported_without_a_guess(self):
        data = {"person_name": "青葉 花子", "person_name_kana": "あおば はなこ"}
        source = "青葉 花子（アオバ ハナコ）\n青葉 花子（アオバ カコ）"
        with patch("src.services.kana_reading.requests.post") as post:
            info = apply_specialist_reading(data, source, [])
        self.assertEqual(data["person_name_kana"], "")
        self.assertEqual(info["reason"], "conflicting_printed_readings")
        post.assert_not_called()

    def test_multiple_personal_email_readings_do_not_select_the_first(self):
        data = {"person_name": "青葉 花子", "person_name_kana": "あおば はなこ"}
        source = "青葉 花子\nhanako.aoba@example.test\nayaka.aoba@example.test"
        with patch("src.services.kana_reading.requests.post") as post:
            info = apply_specialist_reading(data, source, [])
        self.assertEqual(data["person_name_kana"], "")
        self.assertEqual(info["reason"], "conflicting_roman_readings")
        post.assert_not_called()

    def test_printed_kana_wins_when_previous_and_model_disagree(self):
        data = {"person_name": "青葉 花子", "person_name_kana": "あおば はなこ", "email": "a@example.test"}
        previous = {**data, "person_name_kana": "あおば かこ"}
        with patch("src.services.kana_reading.requests.post") as post:
            info = apply_specialist_reading(data, "青葉 花子（アオバ ハナコ）", [], previous)
        self.assertEqual(data["person_name_kana"], "あおば はなこ")
        self.assertEqual(info["status"], "printed")
        post.assert_not_called()

    def test_conflicting_accounts_do_not_leave_an_unrelated_guess_during_boundary_recovery(self):
        data = {"person_name": "青葉花子", "person_name_kana": "ちがう はなこ"}
        source = "青葉花子\nhanako.aoba@example.test\nayaka.aoba@example.test"
        with patch("src.services.kana_reading.requests.post") as post:
            info = apply_specialist_reading(data, source, [])
        self.assertEqual(data["person_name"], "青葉花子")
        self.assertEqual(data["person_name_kana"], "")
        self.assertEqual(info["reason"], "conflicting_roman_readings")
        post.assert_not_called()

    def test_previous_reading_records_the_final_selected_value(self):
        data = {"person_name": "三木 洋亮", "person_name_kana": "", "email": "miki@example.test"}
        previous = {**data, "person_name_kana": "みき ようすけ"}
        response = model_response(["ミキヒロアキ", "ミキヨウスケ"], ["ミキ"])
        with patch("src.services.kana_reading.requests.post", return_value=response):
            info = apply_specialist_reading(data, "三木 洋亮\nmiki@example.test", [], previous)
        self.assertEqual(data["person_name_kana"], "みき ようすけ")
        self.assertEqual(info["selected"], data["person_name_kana"])
        self.assertEqual(info["source"], "previous")

    def test_model_outage_keeps_printed_reading(self):
        data = {"person_name": "浦 伸一", "person_name_kana": "うら しにち"}
        with patch("src.services.kana_reading.requests.post", side_effect=requests.ConnectionError):
            info = apply_specialist_reading(data, "浦 伸一\nshinichi.ura@example.test", [])
        self.assertEqual(data["person_name_kana"], "うら しんいち")
        self.assertEqual(info["status"], "roman")

    def test_unverified_boundary_is_not_invented_when_model_is_unavailable(self):
        with patch("src.services.kana_reading.settings", SimpleNamespace(kana_base_url="")), \
             patch("src.services.extractor._generate_structured_response", return_value='{"person_name":"秦 嘉 一 郎"}'):
            result = extract_card_fields("秦 嘉 一 郎", []).data
        self.assertEqual(result["person_name"], "秦嘉一郎")
        self.assertEqual(result["person_name_kana"], "")
        self.assertEqual(result["_model"]["kana"]["reason"], "surname_boundary_unresolved")

    def test_logos_departments_and_company_substrings_are_rejected(self):
        for proposed, source in [
            ("TOYOTA", "TOYOTA\nトヨタ株式会社"),
            ("本 社 工 場", "本 社 工 場"),
            ("開発部", "開発部"),
            ("山田", "山田株式会社"),
        ]:
            with self.subTest(proposed=proposed), patch("src.services.extractor._generate_structured_response", return_value=json.dumps({"person_name": proposed})):
                result = extract_card_fields(source, []).data
            self.assertEqual(result["person_name"], "")
            self.assertEqual(result["person_name_kana"], "")
