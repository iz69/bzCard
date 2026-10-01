"""Canonical editable and extracted fields shared by the pipeline."""
SCHEMA_KEYS = ['person_name', 'person_name_kana', 'company_name', 'department', 'title', 'postal_code', 'address', 'tel', 'mobile', 'fax', 'email', 'website']
EXTRACTED_JSON_FIELDS = frozenset(SCHEMA_KEYS)
CARD_FIELDS = EXTRACTED_JSON_FIELDS | {"tags", "memo"}
SEARCH_FIELDS = [*SCHEMA_KEYS, "tags", "memo", "ocr_text", "back_ocr_text"]
