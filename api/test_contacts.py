import unittest
from unittest.mock import patch

from src.services.repository import (
    SEARCH_FIELDS, _contacts_from_entries, _contact_search_score,
    _prepare_contact_search, _search_score,
)


def card(card_id: str, **fields):
    return {
        "id": card_id,
        "created_at": fields.pop("created_at", "2026-09-25T00:00:00+00:00"),
        "person_name": "",
        "company_name": "",
        "email": "",
        "mobile": "",
        **fields,
    }


class ContactGroupingTests(unittest.TestCase):
    def test_merges_cards_with_the_same_mobile(self):
        contacts = _contacts_from_entries(
            [
                (card("old", person_name="橋口 秀明", mobile="080-9544-2140"), 0),
                (card("new", person_name="橋口 秀明", email="hashiguchih@example.jp", mobile="08095442140", created_at="2026-09-26T00:00:00+00:00"), 0),
            ],
            None,
        )

        self.assertEqual(len(contacts), 1)
        self.assertEqual(contacts[0]["representative_card_id"], "new")
        self.assertEqual(contacts[0]["card_count"], 2)

    def test_same_name_and_company_without_strong_identifiers_stay_separate(self):
        contacts = _contacts_from_entries(
            [
                (card("first", person_name="橋口 秀明", company_name="三和シャッター工業株式会社"), 0),
                (card("second", person_name="橋口秀明", company_name="三和シヤッター工業 株式会社"), 0),
            ],
            None,
        )

        self.assertEqual(len(contacts), 2)

    def test_search_keeps_a_contact_when_any_card_matches(self):
        contacts = _contacts_from_entries(
            [
                (card("old", person_name="橋口 秀明", mobile="080-9544-2140"), 0),
                (card("new", person_name="橋口 秀明", mobile="080-9544-2140", email="hashiguchih@example.jp"), 1500),
            ],
            "hashiguchih",
        )

        self.assertEqual(len(contacts), 1)
        self.assertEqual(contacts[0]["card_count"], 2)

    def test_transitive_matching_and_distinct_people(self):
        entries = [(card("a", email="a@example.jp"), 0),
                   (card("b", email="a@example.jp", mobile="09012345678"), 0),
                   (card("c", mobile="090-1234-5678"), 0),
                   (card("d", email="different@example.jp"), 0)]
        groups = _contacts_from_entries(entries, None, include_cards=True)
        self.assertEqual({frozenset(c["id"] for c in g["cards"]) for g in groups}, {frozenset("abc"), frozenset("d")})

    def test_shared_identifier_is_normalized_only_once_per_card(self):
        from src.services import repository
        entries = [(card(str(n), email="shared@example.jp"), 0) for n in range(2000)]
        with patch.object(repository, "_contact_identifiers", wraps=repository._contact_identifiers) as identifiers:
            result = _contacts_from_entries(entries, None)
        self.assertEqual(identifiers.call_count, 2000)
        self.assertEqual(result[0]["card_count"], 2000)

    def test_nonrepresentative_status_changes_revision_and_keeps_polling(self):
        old = card("old", email="same@example.jp", status="queued", updated_at="same-second")
        new = card("new", email="same@example.jp", status="ready", created_at="2026-09-29", updated_at="same-second")
        before = _contacts_from_entries([(old, 0), (new, 0)], None)[0]
        after = _contacts_from_entries([({**old, "status": "ready"}, 0), (new, 0)], None)[0]
        self.assertTrue(before["has_in_progress"])
        self.assertFalse(after["has_in_progress"])
        self.assertNotEqual(before["revision"], after["revision"])
        self.assertEqual(before["representative_card_id"], after["representative_card_id"])

    def test_search_scores_preserve_existing_matching_and_ranking(self):
        row = dict.fromkeys(SEARCH_FIELDS, "")
        row.update(person_name="山田 太郎", person_name_kana="やまだたろう", company_name="株式会社テスト",
                   mobile="090-1234-5678", email="Person@Example.jp", ocr_text="東京都 新宿区 ＡＢＣ")
        for query in ("山田", "ヤマダ", "09012345678", "person@example.jp", "株式会社", "ABC", "新宿 山田", "不存在", "", "　"):
            with self.subTest(query=query):
                self.assertEqual(_search_score(row, query), _contact_search_score(row, _prepare_contact_search(query)))


if __name__ == "__main__":
    unittest.main()
