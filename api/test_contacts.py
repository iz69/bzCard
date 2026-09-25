import unittest

from src.services.repository import _contacts_from_entries


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


if __name__ == "__main__":
    unittest.main()
