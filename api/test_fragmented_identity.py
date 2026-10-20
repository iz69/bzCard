"""Fictional regressions for fragmented names and rotated OCR coordinates."""
import copy
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from src.services.extractor import extract_card_fields
from src.services.kana_reading import apply_specialist_reading


class FragmentedIdentityTests(unittest.TestCase):
    def setUp(self):
        settings = patch('src.services.kana_reading.settings', SimpleNamespace(kana_base_url='http://kana:8001'))
        settings.start()
        self.addCleanup(settings.stop)

    def test_surname_account_with_given_initial_verifies_a_three_box_name(self):
        blocks = [
            {'text': '雲', 'box': [100, 100, 145, 150], '_side': 'front'},
            {'text': '原', 'box': [150, 105, 195, 150], '_side': 'front'},
            {'text': '晴 輔', 'box': [200, 102, 350, 152], '_side': 'front'},
        ]
        for account, accepted in [('kumoharah', True), ('kumohara', True), ('kumoharak', False), ('unknown', False)]:
            data = {'person_name': '雲原晴輔', 'person_name_kana': ''}
            def predict(name, family):
                return {'model': 'synthetic', 'candidates': [{'reading': 'クモハラハルスケ'}],
                        'family_candidates': [{'reading': 'クモハラ'}] if family == '雲原' else []}
            with self.subTest(account=account), patch('src.services.kana_reading._predict', side_effect=predict):
                apply_specialist_reading(data, f'雲\n晴 輔\n原\n{account}@example.test', blocks)
            self.assertEqual(data['person_name'], '雲原 晴輔' if accepted else '雲原晴輔')
            self.assertEqual(data['person_name_kana'], 'くもはら はるすけ' if accepted else '')

    def test_sideways_names_are_assembled_without_changing_original_boxes(self):
        horizontal = [
            {'text': '雲原', 'box': [100, 100, 200, 150]},
            {'text': '晴輔', 'box': [230, 100, 330, 150]},
            {'text': '株式会社テスト', 'box': [100, 200, 400, 225]},
            {'text': '営業本部', 'box': [100, 50, 220, 70]},
            {'text': 'kumohara@example.test', 'box': [100, 250, 400, 270]},
        ]
        for scale in (.25, 1, 3):
            blocks = [{**b, 'box': [-b['box'][3] * scale, b['box'][0] * scale,
                                    -b['box'][1] * scale, b['box'][2] * scale], '_side': 'front'} for b in horizontal]
            original = copy.deepcopy(blocks)
            raw = json.dumps({'person_name': '株式会社テスト', 'company_name': '株式会社テスト'})
            prediction = {'model': 'synthetic', 'candidates': [{'reading': 'クモハラハルスケ'}],
                          'family_candidates': [{'reading': 'クモハラ'}]}
            with self.subTest(scale=scale), \
                 patch('src.services.extractor._generate_structured_response', return_value=raw), \
                 patch('src.services.kana_reading._predict', return_value=prediction):
                result = extract_card_fields('株式会社テスト\n営業本部\n雲原\n晴輔\nkumohara@example.test', blocks).data
            self.assertEqual((result['person_name'], result['person_name_kana']), ('雲原 晴輔', 'くもはら はるすけ'))
            self.assertEqual(blocks, original)

    def test_single_box_fragmented_ruby_outranks_an_unrelated_illustration(self):
        blocks = [
            {'text': '雲原律也', 'box': [100, 100, 380, 145], '_side': 'front'},
            {'text': 'くも', 'box': [110, 75, 140, 90], '_side': 'front'},
            {'text': 'はら', 'box': [180, 75, 210, 90], '_side': 'front'},
            {'text': 'りっ', 'box': [250, 75, 280, 90], '_side': 'front'},
            {'text': 'や', 'box': [330, 75, 350, 90], '_side': 'front'},
            {'text': '肉巻', 'box': [600, 10, 630, 90], '_side': 'front'},
            {'text': '三郎', 'box': [640, 10, 670, 90], '_side': 'front'},
        ]
        def predict(name, family):
            if name == '原律也':
                return {'model': 'synthetic', 'candidates': [{'reading': 'ハラリツナリ'}, {'reading': 'ハラリツヤ'}],
                        'family_candidates': []}
            return {'model': 'synthetic',
                    'candidates': [{'reading': 'リツヤ'}] if name == '律也' else [{'reading': 'クモハラリツヤ'}],
                    'family_candidates': [{'reading': {'雲': 'クモ', '雲原': 'クモハラ'}[family]}] if family in ('雲', '雲原') else []}
        original = copy.deepcopy(blocks)
        for proposed in ('株式会社テスト', '肉巻 三郎'):
            with self.subTest(proposed=proposed), \
                 patch('src.services.extractor._generate_structured_response', return_value=json.dumps({'person_name': proposed})), \
                 patch('src.services.kana_reading._predict', side_effect=predict):
                result = extract_card_fields('\n'.join(b['text'] for b in blocks), blocks).data
            self.assertEqual((result['person_name'], result['person_name_kana']), ('雲原 律也', 'くもはら りつや'))
            self.assertEqual(result['_model']['kana']['source'], 'printed')
            self.assertEqual(result['_model']['kana']['ruby_ocr_corrections']['selected'], 'くもはらりつや')
            self.assertEqual(blocks, original)

    def test_spaced_name_without_phonetic_contact_needs_three_model_agreements(self):
        def predict(name, family):
            return {'model': 'synthetic',
                    'candidates': [{'reading': 'ハルスケ'}] if name == '晴輔' else [{'reading': 'キリトウハルスケ'}],
                    'family_candidates': [{'reading': 'キリトウ'}] if family == '霧藤' else []}
        data = {'person_name': '霧藤晴輔', 'person_name_kana': ''}
        with patch('src.services.kana_reading._predict', side_effect=predict):
            info = apply_specialist_reading(data, '霧 藤 晴 輔\nxyz123@example.test', [])
        self.assertEqual(data, {'person_name': '霧藤 晴輔', 'person_name_kana': 'きりとう はるすけ'})
        self.assertEqual(info['verification'], 'family_given_and_full_name_model_match')

    def test_printed_ruby_parts_outweigh_a_wrong_full_name_prediction(self):
        blocks = [
            {'text': '雲原律也', 'box': [100, 120, 400, 180], '_side': 'front'},
            {'text': 'くも はら りつ や', 'box': [100, 85, 400, 110], '_side': 'front'},
        ]
        def predict(name, family):
            return {'model': 'synthetic',
                    'candidates': [{'reading': 'リツナリ'}, {'reading': 'リツヤ'}] if name == '律也' else [{'reading': 'ウンゲンリツヤ'}],
                    'family_candidates': [{'reading': 'クモハラ'}] if family == '雲原' else []}
        with patch('src.services.extractor._generate_structured_response', return_value='{"person_name":"株式会社テスト"}'), \
             patch('src.services.kana_reading._predict', side_effect=predict):
            result = extract_card_fields('株式会社テスト\nくも はら りつ や\n雲原律也', blocks).data
        self.assertEqual((result['person_name'], result['person_name_kana']), ('雲原 律也', 'くもはら りつや'))
        self.assertEqual(result['_model']['kana']['verification'], 'family_given_and_printed_ruby_match')

    def test_conflicting_component_boundaries_are_not_selected_by_order(self):
        def predict(name, family):
            readings = {'雲原律也': 'クモハラリツヤ', '原律也': 'ハラリツヤ', '律也': 'リツヤ'}
            families = {'雲': 'クモ', '雲原': 'クモハラ'}
            return {'model': 'synthetic', 'candidates': [{'reading': readings.get(name, 'ベツ')}],
                    'family_candidates': [{'reading': families[family]}] if family in families else []}
        data = {'person_name': '雲原律也', 'person_name_kana': ''}
        with patch('src.services.kana_reading._predict', side_effect=predict):
            info = apply_specialist_reading(data, '雲 原 律 也', [])
        self.assertEqual(data, {'person_name': '雲原律也', 'person_name_kana': ''})
        self.assertEqual(info['reason'], 'conflicting_model_boundaries')

    def test_adjacent_digit_one_needs_both_roman_components_and_geometry(self):
        name_block = {'text': '雲原 健', 'box': [100, 100, 330, 153], '_side': 'front'}
        one_block = {'text': '1', 'box': [325, 104, 390, 144], '_side': 'front'}
        prediction = {'model': 'synthetic', 'candidates': [{'reading': 'クモハラケンイチ'}],
                      'family_candidates': [{'reading': 'クモハラ'}]}
        for text, digit, accepted in [
            ('KENICHI KUMOHARA\nkenichi.kumohara@example.test', one_block, True),
            ('TARO KUMOHARA\ntaro.kumohara@example.test', one_block, False),
            ('KENICHI KUMOHARA\nkenichi.kumohara@example.test', {**one_block, '_side': 'back'}, False),
            ('KENICHI KUMOHARA\nkenichi.kumohara@example.test', {**one_block, 'box': [500, 104, 565, 144]}, False),
            ('KENICHI KUMOHARA\nkenichi.kumohara@example.test', {**one_block, 'box': [325, 204, 390, 244]}, False),
        ]:
            data = {'person_name': '雲原 健', 'person_name_kana': ''}
            blocks = [name_block, digit]
            original = copy.deepcopy(blocks)
            with self.subTest(digit=digit, text=text), patch('src.services.kana_reading._predict', return_value=prediction):
                info = apply_specialist_reading(data, '雲原 健\n1\n' + text, blocks)
            self.assertEqual(data['person_name'], '雲原 健一' if accepted else '雲原 健')
            if accepted:
                self.assertEqual(info['name_repair']['method'], 'adjacent_ocr_one')
                self.assertEqual(data['person_name_kana'], 'くもはら けんいち')
            self.assertEqual(blocks, original)

    def test_rescan_retains_one_missing_edge_kanji_only_with_both_ids_and_full_roman(self):
        previous = {'person_name': '雲原 晴輔', 'person_name_kana': 'くもはら はるすけ',
                    'email': 'harusuke.kumohara@example.test', 'mobile': '090-0000-1234'}
        raw = {'person_name': '原 晴輔', 'email': previous['email'], 'mobile': previous['mobile']}
        source = '原 晴輔\nHARUSUKE KUMOHARA\nharusuke.kumohara@example.test\n090-0000-1234'
        for old, text, accepted in [
            (previous, source, True),
            ({**previous, 'mobile': '090-0000-5678'}, source, False),
            ({**previous, 'email': 'someone@example.test'}, source, False),
            (previous, source.replace('HARUSUKE KUMOHARA', '').replace('harusuke.kumohara', 'unrelated'), False),
        ]:
            with self.subTest(old=old, text=text), \
                 patch('src.services.extractor._generate_structured_response', return_value=json.dumps(raw)), \
                 patch('src.services.kana_reading._predict', return_value={'candidates': [], 'family_candidates': []}):
                result = extract_card_fields(text, [], previous=old).data
            self.assertEqual(result['person_name'], previous['person_name'] if accepted else '原 晴輔')
            if accepted:
                self.assertEqual(result['person_name_kana'], previous['person_name_kana'])
                self.assertEqual(result['_model']['identity']['reason'], 'roman_email_and_mobile_match_edge_kanji_unreadable')

    def test_rescan_retains_a_misread_last_kanji_with_matching_full_roman_and_both_ids(self):
        previous = {'person_name': '雲原 晴輔', 'person_name_kana': 'くもはら はるすけ',
                    'email': 'harusuke.kumohara@example.test', 'mobile': '090-0000-1234'}
        raw = {**previous, 'person_name': '雲原 晴捕'}
        source = '雲原 晴捕\nHARUSUKE KUMOHARA\nharusuke.kumohara@example.test\n090-0000-1234'
        with patch('src.services.extractor._generate_structured_response', return_value=json.dumps(raw)), \
             patch('src.services.kana_reading._predict') as predict:
            result = extract_card_fields(source, [], previous=previous).data
        self.assertEqual((result['person_name'], result['person_name_kana']), ('雲原 晴輔', 'くもはら はるすけ'))
        self.assertEqual(result['_model']['identity']['source'], 'previous_identity_partial_ocr')
        predict.assert_not_called()

    def test_shorter_handwritten_glyph_remains_part_of_the_same_name_row(self):
        blocks = [
            {'text': '雲', 'box': [100, 100, 180, 200]},
            {'text': '原', 'box': [200, 110, 280, 195]},
            {'text': '律', 'box': [350, 112, 420, 187]},
            {'text': '也', 'box': [460, 122, 530, 178]},
        ]
        def predict(name, family):
            return {'model': 'synthetic',
                    'candidates': [{'reading': 'リツヤ'}] if name == '律也' else [{'reading': 'クモハラリツヤ'}],
                    'family_candidates': [{'reading': 'クモハラ'}] if family == '雲原' else []}
        with patch('src.services.extractor._generate_structured_response', return_value='{"person_name":"株式会社テスト"}'), \
             patch('src.services.kana_reading._predict', side_effect=predict):
            result = extract_card_fields('株式会社テスト\n雲\n原\n律\n也', blocks).data
        self.assertEqual((result['person_name'], result['person_name_kana']), ('雲原 律也', 'くもはら りつや'))

    def test_roman_omitted_long_vowel_does_not_replace_a_compatible_existing_reading(self):
        prediction = {'model': 'synthetic', 'candidates': [{'reading': 'キリトウハルスケ'}],
                      'family_candidates': [{'reading': 'キリトウ'}]}
        raw = {'person_name': '霧戸 晴輔', 'person_name_kana': 'きりと はるすけ',
               'email': 'harusuke.kirito@example.test'}
        source = '霧戸 晴輔\nHARUSUKE KIRITO\nharusuke.kirito@example.test'
        for previous in (None, raw):
            with self.subTest(previous=bool(previous)), \
                 patch('src.services.extractor._generate_structured_response', return_value=json.dumps(raw)), \
                 patch('src.services.kana_reading._predict', return_value=prediction):
                result = extract_card_fields(source, [], previous=previous).data
            self.assertEqual(result['person_name_kana'], 'きりと はるすけ')
            self.assertTrue(result['_model']['kana']['long_vowel_variant_retained'])

    def test_component_boundary_fallback_cannot_override_a_printed_roman_reading(self):
        prediction = {'model': 'synthetic', 'candidates': [{'reading': 'クモバラハルスケ'}, {'reading': 'クモハラハルスケ'}],
                      'family_candidates': [{'reading': 'クモバラ'}, {'reading': 'クモハラ'}]}
        raw = {'person_name': '雲原 晴輔', 'person_name_kana': 'くもばら はるすけ'}
        blocks = [{'text': '雲原 晴輔', 'box': [100, 100, 400, 160]}]
        source = '雲原 晴輔\nharusuke.kumohara@example.test\nHARUSUKE KUMOHARA'
        with patch('src.services.extractor._generate_structured_response', return_value=json.dumps(raw)), \
             patch('src.services.kana_reading._predict', return_value=prediction):
            result = extract_card_fields(source, blocks).data
        self.assertEqual((result['person_name'], result['person_name_kana']), ('雲原 晴輔', 'くもはら はるすけ'))
        self.assertEqual(result['_model']['kana']['source'], 'roman')


if __name__ == '__main__':
    unittest.main()
