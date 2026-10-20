import json
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from src import worker


class WorkerPipelineTests(unittest.TestCase):
    def test_reprocess_and_reextract_share_one_parse_and_save_the_exact_extraction_input(self):
        images = [{'side': 'front', 'ocr_text': '雲原 晴輔',
                   'ocr_blocks_json': json.dumps([{'text': '雲原 晴輔', 'box': [100, 100, 400, 160]}])}]
        card = {'id': 'fictional', 'owner_user_id': 'owner'}
        for job_type in ('reextract', 'reprocess'):
            repository = Mock()
            repository.get_card.return_value = card
            repository.get_card_images.return_value = images
            result = SimpleNamespace(data={'person_name': '雲原 晴輔'}, duration_ms=123)
            with self.subTest(job_type=job_type), patch.object(worker, 'repository', repository), \
                 patch.object(worker, '_process_image') as ocr, \
                 patch.object(worker, 'check_business_card', return_value=SimpleNamespace(is_likely_card=True)) as check, \
                 patch.object(worker, '_combined_ocr_blocks', wraps=worker._combined_ocr_blocks) as blocks, \
                 patch.object(worker, 'extract_card_fields', return_value=result) as extract:
                worker._run_locked_job({'id': 'job', 'card_id': 'fictional', 'type': job_type})
            blocks.assert_called_once()
            extract.assert_called_once()
            repository.save_extraction_result.assert_called_once()
            actual_blocks = extract.call_args.args[1]
            self.assertIs(repository.save_extraction_result.call_args.args[4], actual_blocks)
            self.assertEqual(actual_blocks[0]['_side'], 'front')
            repository.finish_job.assert_called_once_with('job')
            repository.fail_job.assert_not_called()
            self.assertEqual(ocr.call_count, int(job_type == 'reprocess'))
            self.assertEqual(check.call_count, int(job_type == 'reprocess'))

    def test_non_card_stops_before_extraction_and_parsing(self):
        repository = Mock()
        repository.get_card.return_value = {'id': 'fictional', 'owner_user_id': 'owner'}
        repository.get_card_images.return_value = [{'side': 'front', 'ocr_text': 'unrelated document'}]
        with patch.object(worker, 'repository', repository), patch.object(worker, '_process_image'), \
             patch.object(worker, 'check_business_card', return_value=SimpleNamespace(is_likely_card=False, reason='not a card')), \
             patch.object(worker, '_combined_ocr_blocks') as blocks, patch.object(worker, 'extract_card_fields') as extract:
            worker._run_locked_job({'id': 'job', 'card_id': 'fictional', 'type': 'reprocess'})
        blocks.assert_not_called()
        extract.assert_not_called()
        repository.set_card_status.assert_any_call('fictional', 'not_card', 'not a card')
        repository.finish_job.assert_called_once_with('job')
        repository.fail_job.assert_not_called()


if __name__ == '__main__':
    unittest.main()
