"""Complete printed name rows and separate corporate names, memberships and licenses."""
import copy
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from src.services.extractor import extract_card_fields


def blocks():
    return [{'text': text, 'box': box, '_side': 'front'} for text, box in (
        ('屋外広告士/二級建築施工管理技士', [287, 133, 562, 151]),
        ('行政書士 (登録第123456号)', [286, 154, 573, 172]),
        ('夢 野 光', [266, 196, 503, 231]), ('宗', [516, 196, 584, 229]),
        ('代表取締役', [132, 207, 243, 226]),
        ('Yumeno Mitsumune', [375, 244, 562, 261]),
        ('有限', [29, 310, 63, 325]),
        ('青葉堂 (社) 架空県広告協会会員/(社) 全国連盟加盟', [78, 311, 644, 343]),
        ('会社', [28, 327, 63, 344]),
    )]


class CardLayoutEvidenceTests(unittest.TestCase):
    def extract(self, rows, raw, previous=None, available=True):
        source = '\n'.join(row['text'] for row in rows)
        def predict(name, family):
            readings = {'夢野光宗': 'ユメノミツムネ', '光宗': 'ミツムネ'}
            return {'model': 'synthetic',
                    'candidates': [{'reading': readings[name]}] if name in readings else [],
                    'family_candidates': [{'reading': 'ユメノ'}] if family == '夢野' else []}
        with patch('src.services.extractor._generate_structured_response', return_value=json.dumps(raw)), \
             patch('src.services.kana_reading.settings', SimpleNamespace(kana_base_url='http://kana' if available else '')), \
             patch('src.services.kana_reading._predict', side_effect=predict):
            return extract_card_fields(source, rows, previous=previous).data

    def test_partial_model_name_is_completed_even_when_a_bad_previous_value_matches(self):
        rows = blocks() + [{'text': 'yumeno@example.test', 'box': [100, 400, 400, 420], '_side': 'front'}]
        original = copy.deepcopy(rows)
        for proposed in ('夢 野 光', '夢野 光', '夢野光'):
            for old_name in ('', '夢野 光宗', '夢野光', '夢野 光'):
                previous = {'person_name': old_name, 'person_name_kana': '', 'email': 'yumeno@example.test'} if old_name else None
                with self.subTest(proposed=proposed, old_name=old_name):
                    result = self.extract(rows, {'person_name': proposed, 'email': 'yumeno@example.test'}, previous)
                    self.assertEqual((result['person_name'], result['person_name_kana']), ('夢野 光宗', 'ゆめの みつむね'))
                    self.assertEqual(rows, original)

    def test_complete_row_does_not_invent_a_boundary_when_the_model_is_unavailable(self):
        result = self.extract(blocks(), {'person_name': '夢 野 光'}, available=False)
        self.assertEqual((result['person_name'], result['person_name_kana']), ('夢野光宗', ''))

    def test_name_fragment_on_another_side_or_row_is_not_appended(self):
        for side, box in [('back', [516, 196, 584, 229]), ('front', [516, 296, 584, 329]),
                          ('front', [800, 196, 868, 229])]:
            rows = [{'text': '夢野 光', 'box': [266, 196, 503, 231], '_side': 'front'},
                    {'text': '宗', 'box': box, '_side': side}]
            with self.subTest(side=side, box=box):
                result = self.extract(rows, {'person_name': '夢野 光'}, available=False)
                self.assertEqual(result['person_name'], '夢野 光')

    def test_a_separate_complete_person_is_not_extended_with_another_persons_tail(self):
        rows = blocks() + [{'text': '夢野 光', 'box': [266, 500, 503, 535], '_side': 'front'}]
        result = self.extract(rows, {'person_name': '夢野 光'}, available=False)
        self.assertEqual(result['person_name'], '夢野 光')

    def test_stacked_corporate_marker_and_affiliation_are_separated(self):
        for prefix in ('有限', '株式', '合同'):
            for scale in (.5, 1, 3):
                rows = blocks()
                rows[6]['text'] = prefix
                rows = [{**r, 'box': [v * scale for v in r['box']]} for r in rows]
                with self.subTest(prefix=prefix, scale=scale):
                    result = self.extract(rows, {'company_name': '青葉堂 (社)'})
                    self.assertEqual(result['company_name'], prefix + '会社 青葉堂')

    def test_corporate_fragments_on_different_sides_or_far_away_are_not_joined(self):
        for side, box in [('back', [28, 327, 63, 344]), ('front', [28, 427, 63, 444])]:
            rows = blocks()
            rows[8].update(_side=side, box=box)
            with self.subTest(side=side, box=box):
                result = self.extract(rows, {'company_name': '青葉堂 (社)'})
                self.assertEqual(result['company_name'], '青葉堂')

    def test_corporate_affiliation_without_a_membership_label_is_preserved(self):
        rows = [{'text': '有限会社 青葉堂 (社)', 'box': [10, 20, 300, 60]}]
        result = self.extract(rows, {'company_name': '有限会社 青葉堂 (社)'})
        self.assertEqual(result['company_name'], '有限会社 青葉堂 (社)')

    def test_a_single_printed_role_replaces_qualification_fragments(self):
        for title in ('広告士', '屋外広告士', '二級建築施工管理技士', '代表取締役', ''):
            with self.subTest(title=title):
                result = self.extract(blocks(), {'department': '屋外広告', 'title': title})
                self.assertEqual((result['department'], result['title']), ('', '代表取締役'))

    def test_real_department_and_an_independent_title_are_preserved(self):
        rows = blocks() + [{'text': '屋外広告部', 'box': [20, 60, 200, 80]},
                           {'text': '主任研究員', 'box': [20, 90, 200, 110]}]
        result = self.extract(rows, {'department': '屋外広告部', 'title': '主任研究員'})
        self.assertEqual((result['department'], result['title']), ('屋外広告部', '主任研究員'))

    def test_spatial_roman_overrides_a_wrong_given_model_prediction(self):
        rows = blocks()
        rows[5]['text'] = 'Yumeno Mitsuumi'
        def predict(name, family):
            return {'model': 'synthetic', 'candidates': [{'reading': 'ユメノミツコ'}],
                    'family_candidates': [{'reading': 'ユメノ'}] if family == '夢野' else []}
        for scale in (.5, 1, 3):
            scaled = [{**r, 'box': [v * scale for v in r['box']]} for r in rows]
            with self.subTest(scale=scale), \
                 patch('src.services.extractor._generate_structured_response', return_value='{"person_name":"夢 野 光"}'), \
                 patch('src.services.kana_reading.settings', SimpleNamespace(kana_base_url='http://kana')), \
                 patch('src.services.kana_reading._predict', side_effect=predict):
                result = extract_card_fields('\n'.join(r['text'] for r in scaled), scaled).data
            self.assertEqual((result['person_name'], result['person_name_kana']), ('夢野 光宗', 'ゆめの みつうみ'))
            self.assertEqual(result['_model']['kana']['verification'], 'spatial_roman_and_surname_model_match')

    def test_an_unrelated_roman_label_cannot_replace_the_given_model_prediction(self):
        for side, box in [('back', [375, 244, 562, 261]), ('front', [375, 344, 562, 361]),
                          ('front', [675, 244, 862, 261])]:
            rows = blocks()
            rows[5].update(text='Yumeno Mitsuumi', _side=side, box=box)
            with self.subTest(side=side, box=box):
                result = self.extract(rows, {'person_name': '夢野 光宗'})
                self.assertNotEqual(result['person_name_kana'], 'ゆめの みつうみ')

    def test_conflicting_local_roman_labels_are_not_selected_by_order(self):
        rows = blocks() + [{'text': 'Yumeno Mitsuumi', 'box': [375, 244, 562, 261], '_side': 'front'}]
        result = self.extract(rows, {'person_name': '夢野 光宗'})
        self.assertEqual(result['person_name_kana'], '')
        self.assertEqual(result['_model']['kana']['reason'], 'conflicting_spatial_roman_boundaries')

    def test_explicit_kana_keeps_priority_over_a_conflicting_local_roman_label(self):
        rows = blocks() + [{'text': '夢野光宗（ゆめの みつうみ）', 'box': [20, 30, 500, 60], '_side': 'front'}]
        result = self.extract(rows, {'person_name': '夢 野 光'})
        self.assertEqual((result['person_name'], result['person_name_kana']), ('夢野 光宗', 'ゆめの みつうみ'))
        self.assertEqual(result['_model']['kana']['source'], 'printed')

    def test_email_corroborated_reading_is_preserved_when_the_surname_model_is_wrong(self):
        rows = [{'text': '夢野 光宗', 'box': [266, 196, 584, 231], '_side': 'front'}, blocks()[5],
                {'text': 'yumeno.mitsumune@example.test', 'box': [100, 400, 500, 420], '_side': 'front'}]
        model = {'model': 'synthetic', 'candidates': [{'reading': 'ユマノミツムネ'}],
                 'family_candidates': [{'reading': 'ユマノ'}]}
        for reading in ('', 'ゆめの みつむね'):
            raw = {'person_name': '夢野 光宗', 'person_name_kana': reading,
                   'email': 'yumeno.mitsumune@example.test'}
            with self.subTest(reading=reading), \
                 patch('src.services.extractor._generate_structured_response', return_value=json.dumps(raw)), \
                 patch('src.services.kana_reading.settings', SimpleNamespace(kana_base_url='http://kana')), \
                 patch('src.services.kana_reading._predict', return_value=model):
                result = extract_card_fields('\n'.join(r['text'] for r in rows), rows).data
            self.assertEqual((result['person_name'], result['person_name_kana']), ('夢野 光宗', 'ゆめの みつむね'))

    def test_an_adjacent_digit_is_repaired_before_using_the_local_roman_label(self):
        rows = [{'text': text, 'box': box, '_side': 'front'} for text, box in (
            ('雲原 健', [100, 100, 330, 153]), ('1', [325, 104, 390, 144]),
            ('KENICHI KUMOHARA', [100, 170, 380, 190]),
            ('kenichi.kumohara@example.test', [100, 250, 450, 275]),
        )]
        raw = {'person_name': '雲原 健', 'person_name_kana': '', 'email': 'kenichi.kumohara@example.test'}
        model = {'model': 'synthetic', 'candidates': [{'reading': 'クモハラケンイチ'}],
                 'family_candidates': [{'reading': 'クモハラ'}]}
        with patch('src.services.extractor._generate_structured_response', return_value=json.dumps(raw)), \
             patch('src.services.kana_reading.settings', SimpleNamespace(kana_base_url='http://kana')), \
             patch('src.services.kana_reading._predict', return_value=model):
            result = extract_card_fields('\n'.join(r['text'] for r in rows), rows).data
        self.assertEqual((result['person_name'], result['person_name_kana']), ('雲原 健一', 'くもはら けんいち'))
        self.assertEqual(result['_model']['kana']['name_repair']['method'], 'adjacent_ocr_one')


if __name__ == '__main__':
    unittest.main()
