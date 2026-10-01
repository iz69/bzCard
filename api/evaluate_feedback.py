"""Small reproducible feedback evaluation using the configured real LLM.

Always uses a temporary SQLite database, even inside a running API container.
It measures reuse and isolation; this is not an OCR accuracy benchmark.
"""
import json
import tempfile
from pathlib import Path

from src import database
from src.config import settings
from src.services import repository
from src.services.extractor import extract_card_fields


def main():
    old_dir, old_db = settings.data_dir, database.DB_PATH
    with tempfile.TemporaryDirectory(prefix='bzcard-feedback-eval-') as directory:
        object.__setattr__(settings, 'data_dir', Path(directory))
        database.DB_PATH = settings.data_dir / 'bzcard.db'
        try:
            database.init_db()
            owner = repository.create_user('evaluation', 'unused', role='admin')['id']
            other = repository.create_user('held-out-user', 'unused')['id']
            source = '株式会社読み評価\n角田 太郎\na@example.jp'
            baseline = extract_card_fields(source, []).data
            repository.create_card('example', 'cards/example/original.jpg', 'synthetic', 'auto', owner)
            repository.save_extraction_result('example', baseline, 0, source, [])
            # A deliberate uncommon, manually confirmed reading for this person.
            correct = 'すみだ たろう' if baseline['person_name_kana'] != 'すみだ たろう' else 'つのだ たろう'
            repository.update_card_fields('example', {'person_name_kana': correct})
            cases = [
                ('same-person', owner, source, correct),
                ('other-user', other, source, baseline['person_name_kana']),
                ('printed-conflict', owner, '株式会社読み評価\n角田 太郎（カクタ タロウ）\na@example.jp', 'かくた たろう'),
                ('same-surname-new-person', owner, '株式会社読み評価\n角田 花子\nb@example.jp', correct.split()[0]+' はなこ'),
            ]
            results = []
            for label, user_id, text, expected in cases:
                actual = extract_card_fields(text, [], owner_user_id=user_id).data
                result = {'case': label, 'expected': expected, 'actual': actual['person_name_kana'],
                          'applied_count': len(actual['_feedback']['applied_ids']), 'pass': actual['person_name_kana'] == expected}
                if label == 'same-surname-new-person':
                    # Evaluate the corrected surname separately. This probe is
                    # not a benchmark of an unseen given name's pronunciation.
                    reading = actual['person_name_kana'].split()
                    automatic = actual['_automatic']['person_name_kana'].split()
                    result['scope'] = 'surname transfer; given name unchanged'
                    result['expected_surname'] = correct.split()[0]
                    result['pass'] = (actual['person_name'] == '角田 花子' and len(reading) == 2
                                      and reading[0] == correct.split()[0] and len(automatic) == 2
                                      and reading[1] == automatic[1])
                    result['expected'] = (correct.split()[0]+' '+automatic[1]) if len(automatic) == 2 else ''
                if not result['pass']:
                    result['raw'] = actual['_raw']
                    result['automatic'] = actual['_automatic']
                print(json.dumps(result, ensure_ascii=False), flush=True)
                results.append(result)
            print(json.dumps({'model':settings.llm_model, 'baseline_reading':baseline['person_name_kana'],
                              'passes':sum(r['pass'] for r in results), 'cases':len(results)}, ensure_ascii=False), flush=True)
            if not all(r['pass'] for r in results):
                raise SystemExit(1)
        finally:
            object.__setattr__(settings, 'data_dir', old_dir)
            database.DB_PATH = old_db


if __name__ == '__main__':
    main()
