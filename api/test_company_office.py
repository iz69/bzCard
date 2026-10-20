"""Separate printed office affiliations and prefer personal roles to job categories."""
import copy
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from src.services.extractor import extract_card_fields, _extraction_schema, _classify_position
from src.services.fields import SCHEMA_KEYS
from src.services.roles import ROLE_TITLES


def office_blocks(company='青葉機器株式会社', office='北浜営業所'):
    return [{'text': text, 'box': box, '_side': 'front'} for text, box in (
        (office, [100, 30, 250, 50]),
        ('セールスエンジニア', [100, 70, 350, 90]),
        ('所長', [100, 110, 150, 130]),
        ('星丘 晴矢', [100, 140, 380, 180]),
        (company + ' ' + office, [100, 300, 500, 325]),
    )]


class CompanyOfficeTests(unittest.TestCase):
    def test_occupation_is_available_to_constrained_output_but_not_stored_fields(self):
        schema = _extraction_schema(['occupation', 'title'])
        self.assertEqual(set(schema['properties']), {'occupation', 'title'})
        self.assertEqual(set(schema['required']), {'occupation', 'title'})
        self.assertEqual(set(_extraction_schema()['properties']), set(SCHEMA_KEYS))
        self.assertNotIn('occupation', SCHEMA_KEYS)

    def extract(self, rows, **fields):
        raw = {'person_name': '星丘 晴矢', 'person_name_kana': 'ほしおか はるや',
               'company_name': '青葉機器株式会社', 'department': '営業所', 'title': 'セールスエンジニア', **fields}
        original = copy.deepcopy(rows)
        with patch('src.services.extractor._generate_structured_response', return_value=json.dumps(raw)), \
             patch('src.services.kana_reading.settings', SimpleNamespace(kana_base_url='')):
            result = extract_card_fields('\n'.join(row['text'] for row in rows), rows).data
        self.assertEqual(rows, original)
        return result

    def test_grounded_company_is_not_expanded_to_include_the_office(self):
        for scale in (.5, 1, 3):
            rows = [{**r, 'box': [v * scale for v in r['box']]} for r in office_blocks()]
            with self.subTest(scale=scale):
                result = self.extract(rows)
            self.assertEqual(result['company_name'], '青葉機器 株式会社')
            self.assertEqual(result['department'], '北浜営業所')
            self.assertEqual(result['title'], '所長')

    def test_office_is_separated_even_when_the_model_includes_it_in_company(self):
        for department in ('', '営業所', '北浜営業所'):
            with self.subTest(department=department):
                result = self.extract(office_blocks(), company_name='青葉機器株式会社 北浜営業所', department=department)
            self.assertEqual((result['company_name'], result['department']), ('青葉機器 株式会社', '北浜営業所'))

    def test_prefix_legal_form_and_other_office_types_are_supported(self):
        for office in ('北浜支店', '北浜出張所', '北浜事業所', '北浜支社'):
            with self.subTest(office=office):
                result = self.extract(office_blocks('株式会社 青葉機器', office),
                                      company_name='株式会社青葉機器', department='')
            self.assertEqual((result['company_name'], result['department']), ('株式会社 青葉機器', office))

    def test_office_words_inside_a_legal_company_name_are_preserved(self):
        rows = [{'text': '北浜営業所株式会社', 'box': [100, 300, 500, 325]},
                {'text': '北浜営業所', 'box': [100, 30, 250, 50]}]
        result = self.extract(rows, company_name='北浜営業所株式会社', department='', title='')
        self.assertEqual(result['company_name'], '北浜営業所 株式会社')

    def test_a_distinct_printed_department_and_specific_title_are_preserved(self):
        rows = office_blocks() + [{'text': '研究開発部', 'box': [100, 200, 300, 220], '_side': 'front'},
                                  {'text': '課長', 'box': [100, 230, 150, 250], '_side': 'front'}]
        result = self.extract(rows, department='研究開発部', title='課長')
        self.assertEqual((result['department'], result['title']), ('研究開発部', '課長'))

    def test_multiple_offices_do_not_choose_a_department_from_row_order(self):
        rows = office_blocks() + [{'text': '南浜営業所', 'box': [600, 30, 750, 50], '_side': 'front'},
                                  {'text': '青葉機器株式会社 南浜営業所', 'box': [600, 300, 1000, 325], '_side': 'front'}]
        result = self.extract(rows)
        self.assertEqual(result['company_name'], '青葉機器 株式会社')
        self.assertEqual(result['department'], '営業所')

    def test_a_role_on_another_side_or_far_from_the_person_is_not_substituted(self):
        for side, box in (('back', [100, 110, 150, 130]), ('front', [700, 400, 750, 420])):
            rows = office_blocks()
            rows[2].update(_side=side, box=box)
            with self.subTest(side=side, box=box):
                result = self.extract(rows)
            self.assertEqual(result['title'], 'セールスエンジニア')

    def test_conflicting_local_roles_are_not_resolved_by_row_order(self):
        rows = office_blocks() + [{'text': '部長', 'box': [170, 110, 220, 130], '_side': 'front'}]
        result = self.extract(rows)
        self.assertEqual(result['title'], 'セールスエンジニア')

    def test_an_occupation_remains_usable_when_no_personal_role_is_printed(self):
        result = self.extract([r for r in office_blocks() if r['text'] != '所長'])
        self.assertEqual(result['title'], 'セールスエンジニア')

    def test_an_unlisted_title_can_be_inferred_or_supported_by_local_print(self):
        position = '地域統括責任者'
        self.assertNotIn(position, ROLE_TITLES)
        rows = office_blocks()
        rows[2]['text'] = position
        for proposed in (position, 'セールスエンジニア'):
            with self.subTest(proposed=proposed):
                result = self.extract(rows, title=proposed)
            self.assertEqual(result['title'], position)

    def test_model_job_classification_can_support_an_unlisted_position(self):
        rows = office_blocks()
        rows[2]['text'] = '顧客対応統括'
        with patch('src.services.extractor._classify_position', return_value={
                'title': '顧客対応統括', 'occupation': 'セールスエンジニア', 'person_name': '別人 次郎',
                'mobile': '', 'address': '別の住所'}):
            result = self.extract(rows)
        self.assertEqual(result['title'], '顧客対応統括')
        self.assertEqual(result['person_name'], '星丘 晴矢')
        self.assertNotIn('occupation', result)

    def test_roman_name_labels_are_not_reclassified_as_roles(self):
        with patch('src.services.extractor._generate_structured_response') as generate:
            result = _classify_position('星丘 晴矢', '星丘 晴矢\nHaruya Hoshioka',
                                        'セールスエンジニア', 'Haruya Hoshioka')
        self.assertEqual(result['title'], '')
        generate.assert_not_called()
