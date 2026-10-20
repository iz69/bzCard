"""Wide-spacing OCR recovery with generated glyph shapes and fictional names."""
import json
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import cv2
import numpy as np

from src.services.extractor import extract_card_fields
from src.services.kana_reading import apply_specialist_reading
from src.services.ocr import _refine_spaced_name_rows


def sample(count=4, gap=65, background=255, scale=1):
    image = np.full((200, 850, 3), background, dtype=np.uint8)
    ink = (255 - background,) * 3
    for index in range(count):
        x = 40 + index * (45 + gap)
        cv2.rectangle(image, (x, 60), (x + 44, 109), ink, -1)
    box = [40, 60, 85 + (count - 1) * (45 + gap), 110]
    if scale != 1:
        image = cv2.resize(image, None, fx=scale, fy=scale)
        box = [coordinate * scale for coordinate in box]
    return image, {'text': '雲 晴 日', 'box': box, 'font_size': 50 * scale}


def recognizer(texts=('雲原晴輔', '雲原晴輔'), scores=(.99, .98)):
    return SimpleNamespace(recognizer=Mock(side_effect=[
        (SimpleNamespace(contents=[text], scores=[score]), None) for text, score in zip(texts, scores)
    ]))


class SpacedNameOcrTests(unittest.TestCase):
    def test_recovery_uses_visible_glyphs_and_keeps_initial_ocr_and_coordinates(self):
        for scale in (.5, 1, 2):
            for background in (0, 255):
                with self.subTest(scale=scale, background=background):
                    image, block = sample(scale=scale, background=background)
                    before = json.dumps(block)
                    engine = recognizer()
                    result = _refine_spaced_name_rows(engine, image, [block])[0]
                    self.assertEqual(result['text'], '雲 原 晴 輔')
                    self.assertEqual(result['box'], block['box'])
                    self.assertEqual(json.dumps(block), before)
                    evidence = result['_recognition_retry']
                    self.assertEqual(evidence['original_text'], '雲 晴 日')
                    self.assertEqual(evidence['method'], 'compacted_name_spacing')
                    self.assertEqual(len(evidence['glyph_boxes']), 4)
                    self.assertEqual(engine.recognizer.call_count, 2)
                    for call in engine.recognizer.call_args_list:
                        compacted = call.args[0]
                        self.assertLess(compacted.shape[1], block['box'][2] - block['box'][0])

    def test_disagreement_low_scores_missing_glyphs_and_non_names_keep_original(self):
        for texts, scores in ((('雲原晴輔', '雲原晴日'), (.99, .99)),
                              (('雲原晴輔', '雲原晴輔'), (.99, .94)),
                              (('雲原晴', '雲原晴'), (.99, .99)),
                              (('営業本部', '営業本部'), (.99, .99)),
                              (('UNKNOWN', 'UNKNOWN'), (.99, .99))):
            with self.subTest(texts=texts, scores=scores):
                image, block = sample()
                engine = recognizer(texts, scores)
                self.assertEqual(_refine_spaced_name_rows(engine, image, [block]), [block])

    def test_uniform_images_normal_spacing_and_small_decorations_do_not_trigger(self):
        normal, block = sample(gap=5)
        blank = np.full_like(normal, 255)
        wide, wide_block = sample()
        short = wide.copy()
        short[75:] = 255  # A row of thin marks does not form square kanji glyphs.
        for image, source in ((normal, block), (blank, wide_block), (short, wide_block)):
            engine = recognizer()
            self.assertEqual(_refine_spaced_name_rows(engine, image, [source]), [source])
            engine.recognizer.assert_not_called()

    def test_correct_text_is_unchanged_even_when_spaced_recognition_is_retried(self):
        image, block = sample()
        block['text'] = '雲原 晴輔'
        result = _refine_spaced_name_rows(recognizer(), image, [block])
        self.assertEqual(result, [block])
        self.assertNotIn('_recognition_retry', result[0])

    def test_company_fragments_roles_and_other_sides_are_not_reinterpreted(self):
        image, block = sample()
        for text in ('株式会社', '営業本部', '代表取締役'):
            engine = recognizer()
            source = {**block, 'text': text}
            self.assertEqual(_refine_spaced_name_rows(engine, image, [source]), [source])
            engine.recognizer.assert_not_called()
        marker = {'text': '株式会社', 'box': [block['box'][2] + 10, 60, 650, 110]}
        engine = recognizer()
        self.assertEqual(_refine_spaced_name_rows(engine, image, [block, marker]), [block, marker])
        engine.recognizer.assert_not_called()
        engine = recognizer()
        sides = [{**block, '_side': 'front'}, {**marker, '_side': 'back'}]
        self.assertEqual(_refine_spaced_name_rows(engine, image, sides)[0]['text'], '雲 原 晴 輔')

    def test_failure_or_malformed_recognizer_response_retains_initial_ocr(self):
        image, block = sample()
        engine = SimpleNamespace(recognizer=Mock(side_effect=RuntimeError('synthetic failure')))
        with self.assertLogs('bzcard.ocr', level='ERROR'):
            self.assertEqual(_refine_spaced_name_rows(engine, image, [block]), [block])
        malformed = SimpleNamespace(recognizer=Mock(return_value=(SimpleNamespace(contents=[], scores=[]), None)))
        self.assertEqual(_refine_spaced_name_rows(malformed, image, [block]), [block])

    def test_two_to_six_glyphs_do_not_impose_a_surname_length(self):
        for spelling in ('凪宙', '雲原晴', '凪晴輔宙', '雲原晴輔宙', '雲原晴輔宙奏'):
            with self.subTest(length=len(spelling)):
                image, block = sample(count=len(spelling))
                block['text'] = spelling[:-1]
                result = _refine_spaced_name_rows(recognizer((spelling, spelling)), image, [block])[0]
                self.assertEqual(result['text'], ' '.join(spelling))

    def test_restored_letters_allow_previous_one_plus_three_boundary_to_survive(self):
        image, block = sample()
        block['text'] = '凪 晴 日'
        blocks = _refine_spaced_name_rows(recognizer(('凪晴輔宙', '凪晴輔宙')), image, [block])
        source = blocks[0]['text'] + '\nemail:sample@example.test\nmobile:090-0000-0000'
        previous = {'person_name': '凪 晴輔宙', 'person_name_kana': '', 'email': 'sample@example.test'}
        raw = {'person_name': '凪 晴 輔 宙', 'email': 'sample@example.test', 'person_name_kana': ''}
        with patch('src.services.extractor._generate_structured_response', return_value=json.dumps(raw)), \
             patch('src.services.kana_reading.settings', SimpleNamespace(kana_base_url='')):
            result = extract_card_fields(source, blocks, previous=previous).data
        self.assertEqual(result['person_name'], '凪 晴輔宙')
        self.assertEqual(result['_model']['identity']['source'], 'same_person_previous_boundary')

    def test_email_omitted_long_vowel_requires_full_model_support_for_boundary(self):
        def predict(name, family):
            return {'model': 'synthetic',
                    'candidates': [{'reading': 'キリトウハルスケ'}],
                    'family_candidates': [{'reading': 'キリトウ'}] if family == '霧藤' else []}
        for account, accepted in (('kirito', True), ('kiritou', True), ('kirita', False), ('info', False)):
            with self.subTest(account=account), \
                 patch('src.services.kana_reading.settings', SimpleNamespace(kana_base_url='http://kana:8001')), \
                 patch('src.services.kana_reading._predict', side_effect=predict):
                data = {'person_name': '霧藤晴輔', 'person_name_kana': ''}
                info = apply_specialist_reading(data, f'霧 藤 晴 輔\n{account}@example.test', [])
            self.assertEqual(data['person_name'], '霧藤 晴輔' if accepted else '霧藤晴輔')
            self.assertEqual(data['person_name_kana'], 'きりとう はるすけ' if accepted else '')
            if accepted:
                self.assertEqual(info['segmentation'], 'verified')

    def test_unconvertible_account_cannot_validate_a_vowel_only_surname(self):
        def predict(name, family):
            return {'model': 'synthetic', 'candidates': [{'reading': 'ウハルスケソラ'}],
                    'family_candidates': [{'reading': 'ウ'}] if family == '宇' else []}
        data = {'person_name': '宇晴輔宙', 'person_name_kana': ''}
        with patch('src.services.kana_reading.settings', SimpleNamespace(kana_base_url='http://kana:8001')), \
             patch('src.services.kana_reading._predict', side_effect=predict):
            apply_specialist_reading(data, '宇 晴 輔 宙\nxyz@example.test', [])
        self.assertEqual(data, {'person_name': '宇晴輔宙', 'person_name_kana': ''})


if __name__ == '__main__':
    unittest.main()
