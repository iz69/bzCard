"""Combined name/reading regressions across OCR spacing, evidence and failures."""
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import requests

from synthetic_fixtures import _generated_identity

from src.services.extractor import extract_card_fields
from src.services.kana_reading import apply_specialist_reading
from src.services.name_evidence import person_name_is_printed


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

    def test_kumohara_name_survives_a_shared_japanese_and_roman_ocr_block(self):
        line = "雲原 晴輔 Harusuce Kumohara"
        source = f"DEMO\nco.ltd\n営業部 課長\n{line}\nkumohara@example.test\n株式会社fixture"
        blocks = [{"text": line, "box": [52, 344, 722, 410], "_side": "front"}]
        raw = json.dumps({"person_name": "雲原 晴輔", "person_name_kana": "くもばら さつや",
                          "company_name": "DEMO co.ltd", "email": "kumohara@example.test"})
        previous = {"person_name": "雲原 晴輔", "person_name_kana": "くもはら はるすけ",
                    "email": "kumohara@example.test"}
        def predict(_url, *, json, timeout):
            if json["name"] == "晴輔":
                return model_response(["ハルスケ"], [])
            return model_response(["クモハラハルスケ"], ["クモハラ"])
        for old in (None, previous):
            with self.subTest(previous=bool(old)), \
                 patch("src.services.extractor._generate_structured_response", return_value=raw) as generate, \
                 patch("src.services.kana_reading.requests.post", side_effect=predict) as post:
                result = extract_card_fields(source, blocks, previous=old).data
            self.assertEqual(result["person_name"], "雲原 晴輔")
            self.assertEqual(result["person_name_kana"], "くもはら はるすけ")
            self.assertEqual(result["_model"]["identity"]["selected"], "雲原 晴輔")
            self.assertEqual(result["_raw"]["person_name"], "雲原 晴輔")
            generate.assert_called_once()
            self.assertEqual(post.call_count, 2)

    def test_inline_roman_name_supports_recovery_spacing_and_reversed_model_names(self):
        line = "雲原　晴輔 Harusuce Kumohara"
        source = f"株式会社fixture\n{line}"
        for blocks in ([], [{"text": line, "box": [52, 344, 722, 410], "_side": "front"}]):
            for proposed in ("雲原 晴輔", "雲原晴輔", "晴輔 雲原", "株式会社fixture", ""):
                with self.subTest(proposed=proposed, blocks=bool(blocks)), \
                     patch("src.services.extractor._generate_structured_response",
                           return_value=json.dumps({"person_name": proposed})) as generate, \
                     patch("src.services.kana_reading.requests.post",
                           return_value=model_response(["クモハラハルスケ"], ["クモハラ"])):
                    result = extract_card_fields(source, blocks).data
                self.assertEqual(result["person_name"], "雲原 晴輔")
                generate.assert_called_once()

    def test_inline_roman_name_corroborates_a_reading_with_a_two_part_email(self):
        source = "花咲 彩香 Ayaka Hanasaki\nayaka.hanasaki@example.test"
        raw = '{"person_name":"花咲 彩香","person_name_kana":"あおば はなこ"}'
        with patch("src.services.extractor._generate_structured_response", return_value=raw), \
             patch("src.services.kana_reading.requests.post", side_effect=requests.ConnectionError()):
            result = extract_card_fields(source, []).data
        self.assertEqual(result["person_name"], "花咲 彩香")
        self.assertEqual(result["person_name_kana"], "はなさき あやか")
        self.assertEqual(result["_model"]["kana"]["status"], "roman")

    def test_inline_roman_name_does_not_validate_substrings_or_extra_labels(self):
        for line in ("株式会社雲原 晴輔 Harusuce Kumohara",
                     "雲原 晴輔株式会社 Harusuce Kumohara",
                     "営業部 課長 Sales Manager",
                     "営業部 雲原 晴輔 Harusuce Kumohara",
                     "雲原 晴輔 Harusuce Kumohara 営業部",
                     "雲原 晴輔 Harusuce Kumohara Logistics"):
            with self.subTest(line=line):
                blocks = [{"text": line, "box": [52, 344, 722, 410], "_side": "front"}]
                self.assertFalse(person_name_is_printed("雲原 晴輔", line, blocks))
                with patch("src.services.extractor._generate_structured_response",
                           return_value='{"person_name":"雲原 晴輔"}'), \
                     patch("src.services.kana_reading.requests.post") as post:
                    result = extract_card_fields(line, blocks).data
                self.assertEqual(result["person_name"], "")
                post.assert_not_called()
        line = "雲原 晴輔 Harusuce Kumohara"
        for partial in ("雲原", "晴輔", "雲原 晴"):
            self.assertFalse(person_name_is_printed(partial, line, []))

    def test_inline_name_next_to_a_corporate_marker_is_not_a_person(self):
        line = "雲原 晴輔 Harusuce Kumohara"
        blocks = [{"text": line, "box": [52, 344, 722, 410], "_side": "front"},
                  {"text": "株式会社", "box": [750, 344, 950, 410], "_side": "front"}]
        source = f"{line}\n株式会社"
        self.assertFalse(person_name_is_printed("雲原 晴輔", source, blocks))
        with patch("src.services.extractor._generate_structured_response",
                   return_value='{"person_name":"雲原 晴輔"}'), \
             patch("src.services.kana_reading.requests.post") as post:
            result = extract_card_fields(source, blocks).data
        self.assertEqual(result["person_name"], "")
        post.assert_not_called()

    def test_multiple_inline_people_do_not_supply_a_unique_recovery_candidate(self):
        source = "株式会社fixture\n雲原 晴輔 Harusuce Kumohara\n風森 詩美 Utami Kazemori"
        with patch("src.services.extractor._generate_structured_response",
                   return_value='{"person_name":"株式会社fixture"}'), \
             patch("src.services.kana_reading.requests.post") as post:
            result = extract_card_fields(source, []).data
        self.assertEqual(result["person_name"], "")
        post.assert_not_called()

    def test_edano_is_recovered_from_small_widely_spaced_name_blocks(self):
        source = "試験防災株式会社\n取締役執行役員\n県央·県東·東京エリア統括兼経営企画室長\n枝 野\n渉\nedano@example.test"
        original = [
            {"text": "試験防災株式会社", "box": [286, 61, 621, 97]},
            {"text": "取締役執行役員", "box": [286, 120, 443, 141]},
            {"text": "枝 野", "box": [288, 172, 407, 212]},
            {"text": "渉", "box": [537, 174, 576, 208]},
            {"text": "本社認証取得", "box": [34, 350, 140, 367]},
        ]
        raw = json.dumps({"person_name": "試験防災株式会社", "company_name": "試験防災株式会社",
                          "person_name_kana": "あいちじゅうしゃかいていしゃかいていしゃ", "email": "edano@example.test"})
        for scale in (.25, 1, 4):
            for previous in (None, {"person_name": "枝野 渉", "person_name_kana": "えだの わたる", "email": "edano@example.test"}):
                with self.subTest(scale=scale, previous=bool(previous)):
                    blocks = [{**block, "box": [c * scale for c in block["box"]], "_side": "front"}
                              for block in reversed(original)]
                    with patch("src.services.extractor._generate_structured_response", return_value=raw) as generate, \
                         patch("src.services.kana_reading.requests.post", return_value=model_response(["エダノワタル"], ["エダノ"])):
                        result = extract_card_fields(source, blocks, previous=previous).data
                    self.assertEqual(result["person_name"], "枝野 渉")
                    self.assertEqual(result["person_name_kana"], "えだの わたる")
                    self.assertEqual(result["company_name"], "試験防災 株式会社")
                    self.assertEqual(result["_raw"]["person_name"], "試験防災株式会社")
                    generate.assert_called_once()

    def test_wide_name_spacing_does_not_join_opposite_sides_rows_or_distant_columns(self):
        for side, box in (("back", [537, 174, 576, 208]),
                          ("front", [537, 274, 576, 308]),
                          ("front", [2000, 174, 2039, 208])):
            with self.subTest(side=side, box=box):
                blocks = [{"text": "枝 野", "box": [288, 172, 407, 212], "_side": "front"},
                          {"text": "渉", "box": box, "_side": side}]
                raw = '{"person_name":"試験防災株式会社","company_name":"試験防災株式会社"}'
                with patch("src.services.extractor._generate_structured_response", return_value=raw), \
                     patch("src.services.kana_reading.requests.post") as post:
                    result = extract_card_fields("試験防災株式会社\n枝 野\n渉", blocks).data
                self.assertEqual(result["person_name"], "")
                post.assert_not_called()

    def test_printed_two_part_name_in_one_block_is_recovered_without_previous_values(self):
        source = "株式会社テスト·テック\n風森 詩美\n代表取締役\nUtami\nKazemori\nfixture-mail@example.test"
        blocks = [{"text": "風森 詩美", "box": [210, 128, 468, 168], "_side": "front"},
                  {"text": "代表取締役", "box": [56, 140, 194, 159], "_side": "front"}]
        raw = '{"person_name":"株式会社テストテック","person_name_kana":"テストテック 株式会社"}'
        with patch("src.services.extractor._generate_structured_response", return_value=raw), \
             patch("src.services.kana_reading.requests.post", return_value=model_response(["カゼモリウタミ"], ["カゼモリ"])):
            result = extract_card_fields(source, blocks).data
        self.assertEqual(result["person_name"], "風森 詩美")
        self.assertEqual(result["person_name_kana"], "かぜもり うたみ")

    def test_spaced_surname_alone_is_not_recovered_as_a_full_name(self):
        source = "試験防災株式会社\n枝 野"
        blocks = [{"text": "枝 野", "box": [288, 172, 407, 212], "_side": "front"}]
        with patch("src.services.extractor._generate_structured_response", return_value='{"person_name":"試験防災株式会社"}'):
            result = extract_card_fields(source, blocks).data
        self.assertEqual(result["person_name"], "")

    def test_morisaki_rescan_keeps_previous_identity_when_given_name_is_misread(self):
        source = "架空建設株式会社\n一\n執行 投 員\n森 崎\n営業部長\n携帯:080-1234-5678\nmorisaki@example.test"
        blocks = [{"text": "一", "box": [632, 117, 669, 207], "_side": "front"},
                  {"text": "森 崎", "box": [335, 168, 474, 204], "_side": "front"}]
        raw = json.dumps({"person_name": "架空 建設株式会社", "person_name_kana": "かくう けんせつ",
                          "email": "morisaki@example.test", "mobile": "080-1234-5678"})
        for kana in ("", "もりさき がく", "もりさきがく"):
            previous = {"person_name": "森崎 岳", "person_name_kana": kana,
                        "email": "morisaki@example.test", "mobile": "080-1234-5678"}
            with self.subTest(kana=kana), \
                 patch("src.services.extractor._generate_structured_response", return_value=raw), \
                 patch("src.services.kana_reading.requests.post", return_value=model_response(["モリサキガク"], ["モリサキ"])) as post:
                result = extract_card_fields(source, blocks, previous=previous).data
            self.assertEqual(result["person_name"], "森崎 岳")
            self.assertEqual(result["person_name_kana"], "もりさき がく")
            self.assertEqual(result["_raw"]["person_name"], "架空 建設株式会社")
            self.assertEqual(result["_model"]["identity"]["source"], "previous_identity_partial_ocr")
            self.assertEqual(result["_model"]["kana"]["selected"], "もりさき がく")
            if kana == "もりさき がく":
                post.assert_not_called()
            else:
                post.assert_called_once_with("http://kana:8001/predict", json={"name": "森崎岳", "family": "森崎"}, timeout=30)

    def test_retained_identity_with_empty_reading_still_calls_kana_for_rotated_name_boxes(self):
        source = "株式会社 試作社\n春丘\n蒼太\nMobile:070-1234-5678\nharuoka@example.test"
        blocks = [{"text": "春丘", "box": [445, 73, 497, 202], "_side": "front"},
                  {"text": "蒼太", "box": [445, 237, 499, 364], "_side": "front"}]
        raw = json.dumps({"person_name": "シサクシャ 蒼太", "person_name_kana": "ゆうわ そうた",
                          "email": "haruoka@example.test", "mobile": "070-1234-5678"})
        for kana in ("", "はるおかそうた", "春丘 蒼太"):
            previous = {"person_name": "春丘 蒼太", "person_name_kana": kana,
                        "email": "haruoka@example.test", "mobile": "070-1234-5678"}
            with self.subTest(kana=kana), \
                 patch("src.services.extractor._generate_structured_response", return_value=raw), \
                 patch("src.services.kana_reading.requests.post", return_value=model_response(["ハルオカソウタ"], ["ハルオカ"])) as post:
                result = extract_card_fields(source, blocks, previous=previous).data
            self.assertEqual(result["person_name"], "春丘 蒼太")
            self.assertEqual(result["person_name_kana"], "はるおか そうた")
            self.assertFalse(result["_model"]["identity"]["retain_previous_reading"])
            self.assertEqual(result["_model"]["kana"]["source"], "specialist")
            self.assertEqual(result["_automatic"]["person_name_kana"], "はるおか そうた")
            post.assert_called_once_with("http://kana:8001/predict", json={"name": "春丘蒼太", "family": "春丘"}, timeout=30)

    def test_blank_previous_reading_does_not_skip_kana_even_with_a_stale_retention_flag(self):
        for kana in ("", "はるおかそうた", "春丘 蒼太"):
            data = {"person_name": "春丘 蒼太", "person_name_kana": "", "email": "haruoka@example.test"}
            previous = {**data, "person_name_kana": kana}
            with self.subTest(kana=kana), \
                 patch("src.services.kana_reading.requests.post", return_value=model_response(["ハルオカソウタ"], ["ハルオカ"])) as post:
                info = apply_specialist_reading(data, "春丘", [], previous, {"retain_previous_reading": True})
            self.assertEqual(data["person_name_kana"], "はるおか そうた")
            self.assertEqual(info["source"], "specialist")
            post.assert_called_once()

    def test_empty_retained_reading_reports_model_outage_instead_of_a_successful_retention(self):
        data = {"person_name": "春丘 蒼太", "person_name_kana": "", "email": "haruoka@example.test"}
        previous = dict(data)
        with patch("src.services.kana_reading.requests.post", side_effect=requests.ConnectionError()) as post:
            info = apply_specialist_reading(data, "春丘", [], previous, {"retain_previous_reading": True})
        self.assertEqual(data["person_name_kana"], "")
        self.assertEqual(info["status"], "fallback")
        self.assertEqual(info["reason"], "ConnectionError")
        post.assert_called_once()

    def test_partial_ocr_requires_both_printed_identifiers_and_a_standalone_surname(self):
        source = "架空建設株式会社\n一\n森 崎\n携帯:080-1234-5678\nmorisaki@example.test"
        raw = '{"person_name":"架空 建設株式会社","email":"morisaki@example.test","mobile":"080-1234-5678"}'
        previous = {"person_name": "森崎 岳", "person_name_kana": "もりさき がく",
                    "email": "morisaki@example.test", "mobile": "080-1234-5678"}
        cases = [
            (source, {**previous, "email": "someone@example.test"}),
            (source, {**previous, "mobile": "090-1234-5678"}),
            (source.replace("携帯:080-1234-5678", ""), previous),
            (source.replace("morisaki@example.test", ""), previous),
            (source.replace("森 崎", "森崎防災株式会社"), previous),
            (source, {**previous, "person_name": "架空 建設株式会社"}),
            (source, None),
        ]
        for evidence, old in cases:
            with self.subTest(evidence=evidence, previous=old), \
                 patch("src.services.extractor._generate_structured_response", return_value=raw):
                result = extract_card_fields(evidence, [], previous=old).data
            self.assertEqual(result["person_name"], "")
            self.assertEqual(result["person_name_kana"], "")

    def test_partial_ocr_does_not_replace_a_different_grounded_person(self):
        source = "架空建設株式会社\n森崎 明\n携帯:080-1234-5678\nmorisaki@example.test"
        raw = '{"person_name":"森崎 明","email":"morisaki@example.test","mobile":"080-1234-5678"}'
        previous = {"person_name": "森崎 岳", "person_name_kana": "もりさき がく",
                    "email": "morisaki@example.test", "mobile": "080-1234-5678"}
        with patch("src.services.extractor._generate_structured_response", return_value=raw), \
             patch("src.services.kana_reading.requests.post", return_value=model_response(["モリサキアキラ"], ["モリサキ"])):
            result = extract_card_fields(source, [], previous=previous).data
        self.assertEqual(result["person_name"], "森崎 明")
        self.assertEqual(result["person_name_kana"], "もりさき あきら")

    def test_company_rejection_with_multiple_people_retries_without_feedback(self):
        blocks = [{"text": text, "box": box, "_side": "front"} for text, box in (
            ("枝野", [100, 100, 170, 140]), ("渉", [240, 100, 280, 140]),
            ("夏森", [100, 200, 170, 240]), ("綾", [240, 200, 280, 240]),
        )]
        wrong = '{"person_name":"試験防災株式会社"}'
        correct = '{"person_name":"枝野 渉"}'
        with patch("src.services.extractor._generate_structured_response", side_effect=[wrong, correct]) as generate, \
             patch("src.services.kana_reading.requests.post", return_value=model_response(["エダノワタル"], ["エダノ"])):
            result = extract_card_fields("試験防災株式会社\n枝野\n渉\n夏森\n綾", blocks).data
        self.assertEqual(result["person_name"], "枝野 渉")
        self.assertEqual(generate.call_count, 2)
        self.assertIn("会社名を氏名に使わず", generate.call_args.args[0])
        self.assertEqual(result["_feedback"]["fallback"]["rejected_response_text"], wrong)

    def test_ambiguous_people_and_split_company_are_not_selected_by_recovery(self):
        cases = [
            [("枝野", [100, 100, 170, 140]), ("渉", [240, 100, 280, 140]),
             ("夏森", [100, 200, 170, 240]), ("綾", [240, 200, 280, 240])],
            [("試験防災", [100, 100, 270, 140]), ("株式会社", [300, 100, 470, 140]),
             ("常務", [100, 200, 170, 240]), ("取締役", [210, 200, 310, 240])],
            [("試験 防災", [100, 100, 270, 140]), ("株式会社", [300, 100, 470, 140])],
        ]
        for rows in cases:
            with self.subTest(rows=rows):
                source = "試験防災株式会社\n" + "\n".join(text for text, _box in rows)
                blocks = [{"text": text, "box": box, "_side": "front"} for text, box in rows]
                with patch("src.services.extractor._generate_structured_response", return_value='{"person_name":"試験防災株式会社"}'), \
                     patch("src.services.kana_reading.requests.post") as post:
                    result = extract_card_fields(source, blocks).data
                self.assertEqual(result["person_name"], "")
                post.assert_not_called()

    def test_synthetic_names_share_the_same_spacing_and_reading_pipeline(self):
        for seed, (family_length, given_length) in enumerate(((1, 3), (2, 2), (3, 1), (2, 3))):
            person = _generated_identity(seed + 500, family_length, given_length)
            family, given = person['name'].split()
            family_kana, given_kana = person['kana'].split()
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
        source = "夢野 光\n宗\nyumeno.mitsumune@example.test"
        blocks = [
            {"text": "夢野 光", "box": [579, 463, 1178, 582], "_side": "front"},
            {"text": "宗", "box": [1187, 468, 1402, 577], "_side": "front"},
        ]
        raw = '{"person_name":"夢野 光宗","person_name_kana":"ゆめの みつむね"}'
        response = model_response(["ユメノミツムネ"], ["ユメノ", "サト"])
        with patch("src.services.extractor._generate_structured_response", return_value=raw), \
             patch("src.services.kana_reading.requests.post", return_value=response):
            result = extract_card_fields(source, blocks).data
        self.assertEqual(result["person_name"], "夢野 光宗")
        self.assertEqual(result["person_name_kana"], "ゆめの みつむね")

    def test_company_response_is_recovered_from_name_row_with_a_partial_given_name_block(self):
        source = "株式会社 例\n夢野 光\n宗\nyumeno.mitsumune@example.test"
        blocks = [{"text": "夢野 光", "box": [579, 463, 1178, 582], "_side": "front"},
                  {"text": "宗", "box": [1187, 468, 1402, 577], "_side": "front"}]
        def predict(_url, *, json, timeout):
            return model_response(["ユメノミツムネ"], ["ユメノ" if json["family"] == "夢野" else "チガウ"])
        with patch("src.services.extractor._generate_structured_response", return_value='{"person_name":"株式会社 例"}'), \
             patch("src.services.kana_reading.requests.post", side_effect=predict):
            result = extract_card_fields(source, blocks).data
        self.assertEqual(result["person_name"], "夢野 光宗")
        self.assertEqual(result["person_name_kana"], "ゆめの みつむね")

    def test_model_family_alternatives_are_not_conflicting_printed_evidence(self):
        data = {"person_name": "夢野 暁臣", "person_name_kana": ""}
        response = model_response(["ユメノアキオミ"], ["ユメノ", "サト", "サフジ"])
        with patch("src.services.kana_reading.requests.post", return_value=response):
            info = apply_specialist_reading(data, "夢野 暁臣\nakiomi.yumeno@example.test", [])
        self.assertEqual(data["person_name_kana"], "ゆめの あきおみ")
        self.assertNotEqual(info["status"], "uncertain")

    def test_family_first_roman_requires_order_evidence_when_model_is_down(self):
        source = "株式会社 例\n夢野 暁臣\nyumeno.akiomi@example.test"
        raw = '{"person_name":"株式会社 例","person_name_kana":"かぶしき かいしゃ","email":"yumeno.akiomi@example.test"}'
        old = {"person_name": "夢野 暁臣", "person_name_kana": "ゆめの あきおみ",
               "email": "yumeno.akiomi@example.test"}
        for previous, expected in ((None, ""), (old, "ゆめの あきおみ")):
            with self.subTest(previous=bool(previous)), \
                 patch("src.services.extractor._generate_structured_response", return_value=raw), \
                 patch("src.services.kana_reading.requests.post", side_effect=requests.ConnectionError()):
                result = extract_card_fields(source, [], previous=previous).data
            self.assertEqual(result["person_name"], "夢野 暁臣")
            self.assertEqual(result["person_name_kana"], expected)

    def test_existing_reading_survives_model_outage_or_empty_candidates_only_for_same_person(self):
        source = "SAMPLE\n月野 祥介\ntsukino@example.test"
        raw = '{"person_name":"SAMPLE","person_name_kana":"とよた かぶしき","email":"tsukino@example.test"}'
        old = {"person_name": "月野 祥介", "person_name_kana": "つきの しょうすけ", "email": "tsukino@example.test"}
        for model in (requests.ConnectionError(), model_response([], [])):
            for previous, expected in ((old, "つきの しょうすけ"), ({**old, "email": "other@example.test"}, "")):
                with self.subTest(model=type(model).__name__, same=previous == old), \
                     patch("src.services.extractor._generate_structured_response", return_value=raw), \
                     patch("src.services.kana_reading.requests.post", side_effect=model if isinstance(model, Exception) else None,
                           return_value=model):
                    result = extract_card_fields(source, [], previous=previous).data
                self.assertEqual(result["person_name_kana"], expected)
                self.assertEqual(result["_model"]["kana"]["selected"], expected)

    def test_printed_reading_overrides_previous_guess(self):
        old = {"person_name": "月野 祥介", "person_name_kana": "つきの ひろあき", "email": "tsukino@example.test"}
        data = {**old, "person_name_kana": ""}
        with patch("src.services.kana_reading.requests.post") as post:
            info = apply_specialist_reading(data, "月野 祥介（つきの しょうすけ）\ntsukino@example.test", [], previous=old)
        self.assertEqual(data["person_name_kana"], "つきの しょうすけ")
        self.assertEqual(info["status"], "printed")
        post.assert_not_called()

    def test_rescan_does_not_use_a_new_reversed_llm_reading_as_roman_order_evidence(self):
        old = {"person_name": "夢野 暁臣", "person_name_kana": "ゆめの あきおみ", "email": "yumeno.akiomi@example.test"}
        data = {**old, "person_name_kana": "あきおみ さと"}
        with patch("src.services.kana_reading.requests.post", side_effect=requests.ConnectionError()):
            info = apply_specialist_reading(data, "夢野 暁臣\nyumeno.akiomi@example.test", [], previous=old)
        self.assertEqual(data["person_name_kana"], "ゆめの あきおみ")
        self.assertEqual(info["selected"], data["person_name_kana"])

    def test_roman_reading_corrects_a_rare_surname_using_supported_given_name_order(self):
        for roman, email in (("KIRINO HARUNE", "h.kirino"), ("HARUNE KIRINO", "kirino.harune")):
            with self.subTest(roman=roman), \
                 patch("src.services.kana_reading.requests.post", return_value=model_response(["ギリダハルネ"], ["ギリダ"])):
                data = {"person_name": "霧野 晴音", "person_name_kana": ""}
                info = apply_specialist_reading(data, f"霧野 晴音\n{roman}\n{email}@example.test", [])
            self.assertEqual(data["person_name_kana"], "きりの はるね")
            self.assertEqual(info["source"], "roman")

    def test_qualification_and_role_lines_are_never_recovered_as_people(self):
        for label in ("二級建築士", "技術部 リーダー", "機械組付課 係長", "試験 防災"):
            blocks = [{"text": "試験 防災", "box": [100, 100, 250, 140]},
                      {"text": "株式会社", "box": [280, 100, 380, 140]}] if label == "試験 防災" else []
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
            {"text": "星丘", "box": [100, 100, 220, 160]},
            {"text": "晴矢", "box": [250, 100, 370, 160]},
            {"text": "ほしおか", "box": [100, 75, 220, 95]},
            {"text": "はるや", "box": [250, 75, 370, 95]},
        ]
        for scale in (.1, 1, 4):
            with self.subTest(scale=scale), \
                 patch("src.services.extractor._generate_structured_response", return_value='{"person_name":"株式会社 例"}'), \
                 patch("src.services.kana_reading.requests.post") as post:
                scaled = [{**block, "box": [c * scale for c in block["box"]]} for block in blocks]
                data = extract_card_fields("株式会社 例\n星丘\n晴矢\nほしおか\nはるや", scaled).data
            self.assertEqual((data["person_name"], data["person_name_kana"]), ("星丘 晴矢", "ほしおか はるや"))
            post.assert_not_called()
    def test_roman_reading_rejects_wrong_ocr_surname_boundary(self):
        def predict(_url, *, json, timeout):
            family = "ユメノ" if json["family"] == "夢野" else "ユメノマサ"
            return model_response(["ユメノミツムネ"], [family])

        data = {"person_name": "夢野光 宗", "person_name_kana": "ゆめの みつむね"}
        with patch("src.services.kana_reading.requests.post", side_effect=predict):
            info = apply_specialist_reading(data, "夢野 光\n宗\nyumeno.mitsumune@example.test", [])
        self.assertEqual(data["person_name"], "夢野 光宗")
        self.assertEqual(data["person_name_kana"], "ゆめの みつむね")
        self.assertEqual(info["segmentation"], "verified")

    def test_unrelated_english_words_do_not_block_specialist_reading(self):
        data = {"person_name": "夏森 綾", "person_name_kana": "なつもり わか"}
        with patch("src.services.kana_reading.requests.post", return_value=model_response(["ナツモリリョウ"], ["なつもり"])):
            info = apply_specialist_reading(data, "夏森 綾\nBUREAU VERITAS\ninfo@example.test", [])
        self.assertEqual(data["person_name_kana"], "なつもり りょう")
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
        data = {"person_name": "月野 祥介", "person_name_kana": "", "email": "tsukino@example.test"}
        previous = {**data, "person_name_kana": "つきの しょうすけ"}
        response = model_response(["ツキノヒロアキ", "ツキノショウスケ"], ["ツキノ"])
        with patch("src.services.kana_reading.requests.post", return_value=response):
            info = apply_specialist_reading(data, "月野 祥介\ntsukino@example.test", [], previous)
        self.assertEqual(data["person_name_kana"], "つきの しょうすけ")
        self.assertEqual(info["selected"], data["person_name_kana"])
        self.assertEqual(info["source"], "previous")

    def test_model_outage_keeps_printed_reading(self):
        data = {"person_name": "渚 伸一", "person_name_kana": "なぎさ しにち"}
        with patch("src.services.kana_reading.requests.post", side_effect=requests.ConnectionError):
            info = apply_specialist_reading(data, "渚 伸一\nshinichi.nagisa@example.test", [])
        self.assertEqual(data["person_name_kana"], "なぎさ しんいち")
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
            ("SAMPLE", "SAMPLE\nトヨタ株式会社"),
            ("本 社 工 場", "本 社 工 場"),
            ("開発部", "開発部"),
            ("山田", "山田株式会社"),
        ]:
            with self.subTest(proposed=proposed), patch("src.services.extractor._generate_structured_response", return_value=json.dumps({"person_name": proposed})):
                result = extract_card_fields(source, []).data
            self.assertEqual(result["person_name"], "")
            self.assertEqual(result["person_name_kana"], "")
