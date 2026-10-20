"""Reproducible fictional data for tests; never used by application code."""
import random
from src.services.name_evidence import _roman_to_hiragana


def _generated_identity(seed: int, family_length: int = 2, given_length: int = 2) -> dict[str, str]:
    """Build reproducible, fictitious OCR fields without copying a card."""
    rng = random.Random(seed)

    def kanji(start: int, length: int = 2) -> str:
        return "".join(chr(start + rng.randrange(256)) for _ in range(length))

    def roman() -> str:
        return "".join(rng.choice("kmnr") + rng.choice("aeiou") for _ in range(2)).upper()

    family, given = kanji(0x4E00, family_length), kanji(0x5200, given_length)
    family_roman, given_roman = roman(), roman()
    return {
        "name": f"{family} {given}",
        "kana": f"{_roman_to_hiragana(family_roman)} {_roman_to_hiragana(given_roman)}",
        "roman": f"{family_roman} {given_roman}",
        "family_roman": family_roman,
        "given_roman": given_roman,
        "email": f"{given_roman[0].lower()}.{family_roman.lower()}@example.test",
        "store": f"{kanji(0x5600)}店",
        "company": f"{kanji(0x5A00)}サービス",
    }
