"""Decision precedence and repeated work in the actual extraction pipeline."""
import copy
import json
import runpy
import unittest
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import requests

from kana.prediction_cache import CachedPredictor
import kana.prediction_cache as prediction_cache_module
from src.services import kana_reading, name_evidence
from src.services.extractor import extract_card_fields


def response(payload):
    return SimpleNamespace(raise_for_status=lambda: None, json=lambda: copy.deepcopy(payload))


class ReadingPrecedenceTests(unittest.TestCase):
    def setUp(self):
        config = patch.object(kana_reading, 'settings', SimpleNamespace(kana_base_url='http://kana:8001'))
        config.start()
        self.addCleanup(config.stop)

    def test_explicit_kana_precedes_a_roman_backed_digit_one_repair(self):
        blocks = [{'text': '雲原 健', 'box': [100, 100, 330, 153]},
                  {'text': '1', 'box': [325, 104, 390, 144]}]
        source = '雲原 健（くもはら けん）\n雲原 健\n1\nKENICHI KUMOHARA\nkenichi.kumohara@example.test'
        raw = json.dumps({'person_name': '雲原 健', 'person_name_kana': ''})
        model = {'model': 'synthetic', 'candidates': [{'reading': 'クモハラケンイチ'}],
                 'family_candidates': [{'reading': 'クモハラ'}]}
        original = copy.deepcopy(blocks)
        with patch('src.services.extractor._generate_structured_response', return_value=raw), \
             patch.object(kana_reading.requests, 'post', return_value=response(model)) as post:
            result = extract_card_fields(source, blocks).data
        self.assertEqual((result['person_name'], result['person_name_kana']), ('雲原 健', 'くもはら けん'))
        self.assertEqual(result['_model']['kana']['status'], 'printed')
        post.assert_not_called()
        self.assertEqual(blocks, original)

    def test_conflicting_printed_kana_precedes_a_digit_one_repair(self):
        blocks = [{'text': '雲原 健', 'box': [100, 100, 330, 153]},
                  {'text': '1', 'box': [325, 104, 390, 144]}]
        source = ('雲原 健（くもはら けん）\n雲原 健（くもはら まさる）\n'
                  '雲原 健\n1\nKENICHI KUMOHARA\nkenichi.kumohara@example.test')
        model = {'candidates': [{'reading': 'クモハラケンイチ'}], 'family_candidates': [{'reading': 'クモハラ'}]}
        with patch('src.services.extractor._generate_structured_response', return_value='{"person_name":"雲原 健"}'), \
             patch.object(kana_reading.requests, 'post', return_value=response(model)) as post:
            result = extract_card_fields(source, blocks).data
        self.assertEqual((result['person_name'], result['person_name_kana']), ('雲原 健', ''))
        self.assertEqual(result['_model']['kana']['reason'], 'conflicting_printed_readings')
        post.assert_not_called()

    def test_correction_does_not_fill_an_unresolved_printed_roman_reading(self):
        raw = {'person_name': '雲原 晴輔', 'email': 'harusuce.kumohara@example.test'}
        correction = {'id': 'confirmed', 'context': raw, 'corrected_value': 'くもはら はるすけ',
                      'automatic_value': '', 'person_example': True, 'reading_rules': []}
        source = '雲原 晴輔\nHARUSUCE KUMOHARA\nharusuce.kumohara@example.test'
        with patch('src.services.extractor._generate_structured_response', return_value=json.dumps(raw)), \
             patch('src.services.extractor.relevant_corrections', return_value=[correction]), \
             patch.object(kana_reading.requests, 'post', side_effect=requests.ConnectionError):
            result = extract_card_fields(source, []).data
        self.assertEqual(result['person_name_kana'], '')
        self.assertEqual(result['_model']['kana']['status'], 'uncertain')
        self.assertEqual(result['_feedback']['applied_ids'], [])
        self.assertEqual(result['_model']['kana']['selected'], result['person_name_kana'])

    def test_confirmed_correction_can_still_replace_a_guess_without_printed_reading(self):
        raw = {'person_name': '雲原 晴輔', 'email': 'sample@example.test'}
        correction = {'id': 'confirmed', 'context': raw, 'corrected_value': 'くもはら はるすけ',
                      'automatic_value': '', 'person_example': True, 'reading_rules': []}
        with patch('src.services.extractor._generate_structured_response', return_value=json.dumps(raw)), \
             patch('src.services.extractor.relevant_corrections', return_value=[correction]), \
             patch.object(kana_reading.requests, 'post', side_effect=requests.ConnectionError):
            result = extract_card_fields('雲原 晴輔\nsample@example.test', []).data
        self.assertEqual(result['person_name_kana'], 'くもはら はるすけ')
        self.assertEqual(result['_model']['kana']['source'], 'confirmed_correction')
        self.assertEqual(result['_feedback']['applied_ids'], ['confirmed'])


class PredictionReuseTests(unittest.TestCase):
    def setUp(self):
        config = patch.object(kana_reading, 'settings', SimpleNamespace(kana_base_url='http://kana:8001'))
        config.start()
        self.addCleanup(config.stop)

    def test_boundary_fallback_never_repeats_the_same_request_within_one_extraction(self):
        empty = {'model': 'synthetic', 'candidates': [], 'family_candidates': []}
        for _ in range(2):
            data = {'person_name': '雲原 晴輔', 'person_name_kana': 'くもはら はるすけ'}
            with patch.object(kana_reading.requests, 'post', return_value=response(empty)) as post:
                info = kana_reading.apply_specialist_reading(data, '雲原 晴輔', [])
            calls = [(c.kwargs['json']['name'], c.kwargs['json']['family']) for c in post.call_args_list]
            self.assertGreater(len(calls), 1)
            self.assertEqual(len(calls), len(set(calls)))
            self.assertEqual(data['person_name_kana'], 'くもはら はるすけ')
            self.assertEqual(info['status'], 'fallback')

    def test_model_outage_is_attempted_once_and_retried_on_the_next_extraction(self):
        blocks = [{'text': '雲原 健', 'box': [100, 100, 330, 153]},
                  {'text': '1', 'box': [325, 104, 390, 144]}]
        source = '雲原 健\n1\nKENICHI KUMOHARA\nkenichi.kumohara@example.test'
        for failure in (requests.ConnectionError, requests.Timeout):
            with self.subTest(failure=failure), patch.object(kana_reading.requests, 'post', side_effect=failure) as post:
                for _ in range(2):
                    data = {'person_name': '雲原 健', 'person_name_kana': ''}
                    kana_reading.apply_specialist_reading(data, source, blocks)
                self.assertEqual(post.call_count, 2)

    def test_model_cache_is_bounded_and_does_not_share_mutable_candidates(self):
        model = Mock(side_effect=lambda name: [{'reading': name, 'log_probability': -.1}])
        predict = CachedPredictor(model, maxsize=2)
        first = predict('甲')
        first[0]['reading'] = 'changed'
        self.assertEqual(predict('甲')[0]['reading'], '甲')
        predict('乙')
        predict('甲')  # Keep 甲; the next insertion evicts 乙.
        predict('丙')
        predict('乙')
        self.assertEqual([c.args[0] for c in model.call_args_list], ['甲', '乙', '丙', '乙'])
        self.assertEqual(predict(''), [])

    def test_new_model_instance_does_not_reuse_an_old_models_predictions(self):
        first = CachedPredictor(lambda name: [{'reading': 'old'}])
        second = CachedPredictor(lambda name: [{'reading': 'new'}])
        self.assertEqual(first('雲原')[0]['reading'], 'old')
        self.assertEqual(second('雲原')[0]['reading'], 'new')

    def test_kana_http_handler_reuses_a_full_name_across_different_surname_requests(self):
        model = Mock()
        model.predict.side_effect = lambda name: [{'reading': name, 'log_probability': -.1}]
        modules = {'torch': SimpleNamespace(set_num_threads=Mock()),
                   'model': SimpleNamespace(KanaModel=Mock(return_value=model), MODEL_VERSION='synthetic'),
                   'prediction_cache': prediction_cache_module}
        with patch.dict('sys.modules', modules), patch('http.server.ThreadingHTTPServer'), patch('logging.basicConfig'):
            server = runpy.run_path(str(Path(__file__).parent / 'kana' / 'server.py'))
        handler_class = server['Handler']
        for family in ('雲原', '雲'):
            body = json.dumps({'name': '雲原晴輔', 'family': family}).encode()
            handler = handler_class.__new__(handler_class)
            handler.path = '/predict'
            handler.headers = {'Content-Length': str(len(body))}
            handler.rfile = BytesIO(body)
            handler.respond = Mock()
            handler.do_POST()
            status, payload = handler.respond.call_args.args
            self.assertEqual(status, 200)
            self.assertEqual(payload['model'], 'synthetic')
            self.assertEqual(payload['candidates'][0]['reading'], '雲原晴輔')
            self.assertEqual(payload['family_candidates'][0]['reading'], family)
        self.assertEqual([call.args[0] for call in model.predict.call_args_list], ['雲原晴輔', '雲原', '雲'])


class EvidenceReuseTests(unittest.TestCase):
    def test_geometry_is_reused_only_within_a_scope_and_for_the_same_input(self):
        blocks = [{'text': '雲原', 'box': [100, 100, 200, 150]},
                  {'text': '晴輔', 'box': [220, 100, 320, 150]}]
        original = copy.deepcopy(blocks)
        with patch.object(name_evidence, '_box_coordinates', wraps=name_evidence._box_coordinates) as boxes:
            with name_evidence.name_evidence_scope():
                self.assertEqual(name_evidence._spatial_name_candidates(blocks), ['雲原晴輔'])
                calls = boxes.call_count
                self.assertGreater(calls, 0)
                self.assertEqual(name_evidence._spatial_name_candidates(blocks), ['雲原晴輔'])
                self.assertEqual(boxes.call_count, calls)
                name_evidence._spatial_name_candidates(copy.deepcopy(blocks))
                self.assertGreater(boxes.call_count, calls)
            calls = boxes.call_count
            name_evidence._spatial_name_candidates(blocks)
            self.assertGreater(boxes.call_count, calls)
        self.assertEqual(blocks, original)
        blocks[1]['text'] = '律也'
        self.assertEqual(name_evidence._spatial_name_candidates(blocks), ['雲原律也'])

    def test_exception_does_not_leave_an_evidence_cache_for_the_next_job(self):
        blocks = [{'text': '雲原', 'box': [100, 100, 200, 150]},
                  {'text': '晴輔', 'box': [220, 100, 320, 150]}]
        with self.assertRaises(RuntimeError), name_evidence.name_evidence_scope():
            name_evidence._spatial_name_candidates(blocks)
            raise RuntimeError('synthetic failure')
        blocks[1]['text'] = '律也'
        with name_evidence.name_evidence_scope():
            self.assertEqual(name_evidence._spatial_name_candidates(blocks), ['雲原律也'])


if __name__ == '__main__':
    unittest.main()
