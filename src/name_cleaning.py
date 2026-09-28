"""Remove numeric building annotations from the display name, retaining raw data."""
from __future__ import annotations

import re
import unicodedata

VERSION = "numeric-annotations-1.0.0"
# Address stems ending in 제 in the current corpus, plus ordinary complete words.
# Preserve their final syllable when it touches a phase number (거제2차).
JE_ENDING_WORDS = ("강제", "거제", "김제", "연제", "인제", "지제", "해제", "홍제", "효제", "백제", "황제", "국제", "경제", "형제")
_BRACKETS = re.compile(r"\([^()]*\)|\[[^\[\]]*\]|\{[^{}]*\}")
_NUMBER_INFO = re.compile(r"[0-9제차단지블록동호층번지\s,·;:/~\-–—.ㆍ]+")
_UNIT = re.compile(r"(?:제\s*)?[0-9]+(?:\s*[,·.ㆍ/&~–—-]\s*[0-9]+)*\s*(?:단지|블록|번지|차|동|호|층)")
_STANDALONE = re.compile(r"(?<![\w.])[0-9]+(?:\s*[-~–—]\s*[0-9]+)*(?![\w.])")
_TRAILING = re.compile(r"(?<=[가-힣)\]}])[0-9]+$")


def clean_apartment_name(value: str | None) -> str:
    """Clean annotation-shaped numbers; do not strip digits inside H1/3.1 names.

    IDs and original names are never modified. Equal cleaned names do not imply
    the same complex. NFKC handles full-width parentheses and digits.
    """
    text = unicodedata.normalize("NFKC", str(value or ""))

    def bracket(match):
        inside = match.group()[1:-1]
        if re.search(r"[0-9]", inside) and _NUMBER_INFO.fullmatch(inside):
            return " "
        return match.group()

    # Iterate so adjacent/nested empty annotation wrappers cannot survive.
    previous = None
    while text != previous:
        previous = text
        text = _BRACKETS.sub(bracket, text)
        text = _UNIT.sub(lambda m: "제 " if m.group().startswith("제") and text[:m.start() + 1].endswith(JE_ENDING_WORDS) else " ", text)
        text = _STANDALONE.sub(" ", text)
        text = re.sub(r"\s+", " ", text).strip()
        text = _TRAILING.sub("", text)
        text = re.sub(r"([([{])\s*[,·;:/~\-–—.ㆍ]*\s*", r"\1", text)
        text = re.sub(r"\s*[,·;:/~\-–—.ㆍ]*\s*([)\]}])", r"\1", text)
        text = re.sub(r"\(\s*\)|\[\s*\]|\{\s*\}", " ", text)
        text = re.sub(r"\s+", " ", text).strip(" ,·;:/~\u002d–—.ㆍ")
    return text
