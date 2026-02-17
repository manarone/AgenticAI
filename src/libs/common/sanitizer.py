import re

DANGEROUS_PATTERNS = [
    r'ignore\s+all\s+previous\s+instructions',
    r'reveal\s+your\s+system\s+prompt',
    r'print\s+all\s+secrets',
    r'bypass\s+approval',
    r'exfiltrate',
]


def sanitize_input(text: str) -> tuple[str, bool, list[str]]:
    normalized = text.strip()
    matches: list[str] = []
    for pattern in DANGEROUS_PATTERNS:
        if re.search(pattern, normalized, re.IGNORECASE):
            matches.append(pattern)

    flagged = bool(matches)
    if flagged:
        normalized = '[FILTERED] Potential prompt-injection attempt detected.'
    return normalized, flagged, matches
