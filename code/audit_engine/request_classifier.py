"""
Request Classifier — determines whether a request_text authorizes deletion.

Uses deterministic pattern matching across multiple languages.
Falls back gracefully for ambiguous cases without requiring an external model.
"""
from __future__ import annotations

import re
from dataclasses import dataclass


# ---------------------------------------------------------------------------
# Deletion patterns (multiple languages)
# ---------------------------------------------------------------------------

_DELETION_PATTERNS: list[re.Pattern] = [
    re.compile(p, re.IGNORECASE | re.UNICODE) for p in [
        # English
        r"\bdelet\w*\b",
        r"\berase\w*\b",
        r"\bremov\w*\b",
        r"\bwipe\b",
        r"\bpurge\b",
        r"\bRTBF\b",
        r"\bright.to.be.forgotten\b",
        r"\bsuppression\b",
        r"\bgdpr.request\b",
        # Close account as deletion
        r"\bclose\s+(this\s+|my\s+|the\s+)?account\b",
        r"\bclos(ing|ed)\s+(this\s+|my\s+|the\s+)?account\b",
        r"\bshut\s+down\s+(this\s+|my\s+|the\s+)?account\b",
        r"\bcancel\s+(my\s+|this\s+)?account\b",
        r"\bdeactivate\s+(my\s+|this\s+)?(account)?\b",
        r"\bpermanently?\s+remov\w*\b",
        r"\bpermanently?\s+delet\w*\b",
        r"\bpermanent\s+account.deletion\b",
        r"\bpermanent.deletion\b",
        r"\bpersonal.data.deletion\b",
        r"\baccount.deletion\b",
        r"\bdelete.my.account\b",
        r"\bdelete.this.account\b",
        r"\bremove.my.account\b",
        r"\bremove.the.account\b",
        r"\bremove.records\b",
        r"\bremove.my.profile\b",
        r"\bremove.the.profile\b",
        r"\bremove.personal\b",
        r"\bdeleted.account\b",  # "I deleted my account"
        r"\berased.account\b",
        # French
        r"\bsupprimer\b",
        r"\bsupprim\w*\b",
        r"\beffacer\b",
        r"\beffac\w*\b",
        r"\bsuppr\w*\b",
        r"\bsuppression\s+.*(compte|donn[eé]es|profil)\b",
        r"\bdemande.*suppression\b",
        # Spanish
        r"\beliminar\b",
        r"\beliminaci[oó]n\b",
        r"\bborrar\b",
        r"\bborr[ao]\b",
        r"\bsuprimir\b",
        r"\bbaja\b.*(cuenta|perfil|datos)\b",
        r"\beliminar\s+(mi\s+)?(cuenta|perfil|datos)\b",
        # German
        r"\blösch\w*\b",
        r"\bwiderruf\w*\b",
        r"\bkonto.lösch\w*\b",
        # Portuguese
        r"\bapagar\b",
        r"\bexclu[ií]r\b",
        r"\bexclu[ií]\b",
        r"\bdeletar\b",
        r"\bexclusão\b",
        r"\bremoção\b",
        # Japanese unicode
        r"削除",
        r"消去",
        r"退会",
        r"アカウント削除",
        # Korean
        r"삭제",
        r"계정\s*삭제",
        # Arabic
        r"حذف",
        r"مسح",
        # Turkish
        r"\bsilme\b",
        r"\bsil\b",
        # Indonesian/Malay
        r"\bhapus\b",
    ]
]

# Patterns that indicate the request is about something OTHER than deletion
_NON_DELETION_PATTERNS: list[re.Pattern] = [
    re.compile(p, re.IGNORECASE) for p in [
        # Explicit data access/export
        r"\bdownload\s+(my\s+)?data\b",
        r"\bexport\s+(my\s+)?data\b",
        r"\brequest.*(copy|access|export)\b",
        r"\bdata.portability\b",
        r"\bsend.me.my.data\b",
        # Correction-only
        r"\bcorrect\b.{0,30}(my|the)\b.{0,30}(name|email|address|phone|data)\b",
        r"\bupdate\s+(my\s+)?(email|password|name|address|phone)\b",
        r"\bchange\s+(my\s+)?(email|password|name|address|phone)\b",
        # Marketing opt-out only
        r"\bopt[\-\s]out\s+(of\s+)?(marketing|email|newsletter|promotions)\b",
        r"\bunsubscribe\b",
        # Access only
        r"\baccess\s+to\s+(my\s+)?data\b",
        r"\bview\s+(my\s+)?data\b",
    ]
]

# Phrases that indicate intent to inspect / audit deletion state
# (these ARE deletion-adjacent and should be treated as deletion requests)
_AUDIT_DELETION_PATTERNS: list[re.Pattern] = [
    re.compile(p, re.IGNORECASE) for p in [
        r"\bverify.*delet\w*\b",
        r"\bconfirm.*delet\w*\b",
        r"\bcheck.*delet\w*\b",
        r"\bprofile.is.gone\b",
        r"\baccount.*(still\s+)?exist\w*\b",
        r"\bdata.*(already.*)?(remov|delet)\w*\b",
        r"\binspect.*system\b.{0,60}(delet|remov|gone|absent)\b",
        r"\baudit.*delet\w*\b",
        r"\bstill\s+(recogni|found|exist)\w*\b",
        r"\bright\s+to\s+(erasure|deletion|be\s+forgotten)\b",
    ]
]


@dataclass
class ClassificationResult:
    is_deletion_request: bool
    confidence: str          # "high" | "medium" | "low"
    reason: str


def classify_request(request_text: str) -> ClassificationResult:
    """
    Classify whether the supplied request_text authorizes deletion.

    Priority order:
    1. Strong deletion patterns → deletion (high confidence)
    2. Audit/verify deletion patterns → deletion (medium confidence)
    3. Only non-deletion patterns → not deletion (high confidence)
    4. No signal → not deletion (low confidence)
    """
    text = request_text or ""

    deletion_hits = [p.pattern for p in _DELETION_PATTERNS if p.search(text)]
    audit_hits = [p.pattern for p in _AUDIT_DELETION_PATTERNS if p.search(text)]
    non_deletion_hits = [p.pattern for p in _NON_DELETION_PATTERNS if p.search(text)]

    if deletion_hits:
        return ClassificationResult(
            is_deletion_request=True,
            confidence="high",
            reason=f"Deletion keywords matched: {deletion_hits[:3]}",
        )

    if audit_hits:
        return ClassificationResult(
            is_deletion_request=True,
            confidence="medium",
            reason=f"Deletion audit patterns matched: {audit_hits[:3]}",
        )

    if non_deletion_hits:
        return ClassificationResult(
            is_deletion_request=False,
            confidence="high",
            reason=f"Non-deletion patterns matched without deletion signals: {non_deletion_hits[:3]}",
        )

    return ClassificationResult(
        is_deletion_request=False,
        confidence="low",
        reason="No deletion or close patterns found in request text.",
    )
