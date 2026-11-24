"""Masking of personal data in free text before it is stored (plans/T3-48.md, docs/log-schema.md 3).

Free text reaches the log in three columns only: request_logs.question, request_rewrites.text (the question the agent
rewrote, T3-50) and ui_events.reason. All go through mask() first.
The patterns are the ones a statistics question can carry by mistake: an e-mail address, a phone number, a resident
registration number, a card number. Numbers a statistics question needs (years like 2023, ranges like 2015~2023,
counts like 1,234,567) must stay; the tests hold both sides.
"""

from __future__ import annotations

import re

# Order matters: the longer, more specific number shapes go first so a card number is not read as two phone numbers.
_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("[이메일]", re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")),
    ("[주민번호]", re.compile(r"(?<!\d)\d{6}\s?-\s?[1-8]\d{6}(?!\d)")),
    ("[카드번호]", re.compile(r"(?<!\d)\d{4}([ -]?)\d{4}\1\d{4}\1\d{4}(?!\d)")),
    # mobile 010-1234-5678 / 01012345678, area 02-123-4567 / 031-1234-5678, with '-', '.' or ' ' between parts
    ("[전화번호]", re.compile(r"(?<!\d)(?:01[016789]|0[2-6]\d?)[-. ]?\d{3,4}[-. ]?\d{4}(?!\d)")),
)


def mask(text: str | None) -> str | None:
    """`text` with personal data replaced by a label ([이메일], [주민번호], [카드번호], [전화번호]); None stays None.

    Side effects: none.
    """
    if text is None:
        return None
    for label, pattern in _PATTERNS:
        text = pattern.sub(label, text)
    return text
