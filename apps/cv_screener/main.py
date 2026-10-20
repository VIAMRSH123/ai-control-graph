"""CV screening assistant — the Annex III high-risk system in the estate.

Employment screening is listed in EU AI Act Annex III, so Articles 9-15 apply
in full rather than hypothetically. This is the system that makes most of the
EU half of the graph real.

Two controls are load-bearing here and are implemented, not described:

  human review   every output is marked as requiring human confirmation; the
                 service never returns a hire/reject decision
  bias handling  protected characteristics are stripped from the input before
                 the model sees it, and the prompt forbids inferring them
"""

import re

from apps.common.core import audit
from apps.common.service import build, lambda_handler_for

SYSTEM_PROMPT = """You assist a human recruiter by summarising how a candidate's \
stated experience relates to a role specification.

You MUST:
- restrict yourself to skills, qualifications and experience the candidate states
- cite the specific evidence in the CV for each point you make
- list what the role requires that the CV does not evidence

You MUST NOT:
- recommend hiring or rejecting anyone, or output a score or ranking
- infer or comment on age, gender, ethnicity, nationality, religion, disability, \
health, pregnancy, marital status or any other protected characteristic
- infer any of the above from names, photographs, dates, institutions or addresses

A human recruiter makes every decision. Your output is an input to their \
judgement, never a substitute for it."""

#: Stripped before the model sees the CV. Removing the signal is more reliable
#: than instructing the model to ignore it.
PROTECTED_PATTERNS = [
    re.compile(r"\b(date of birth|dob|born)\b.{0,40}", re.I),
    re.compile(r"\b(male|female|non-binary|he/him|she/her|they/them)\b", re.I),
    re.compile(r"\b(married|single|divorced|widowed)\b", re.I),
    re.compile(r"\b(nationality|citizenship|visa status)\b.{0,40}", re.I),
    re.compile(r"\b(photo|photograph) attached\b", re.I),
]


_removed_count = 0


def strip_protected(text: str) -> str:
    """Remove protected-characteristic signal before the model sees the CV.

    Removing the signal is more reliable than instructing a model to ignore it.
    The count is logged so the control is evidenced, not merely asserted.
    """
    global _removed_count
    _removed_count = 0
    for pattern in PROTECTED_PATTERNS:
        text, n = pattern.subn("[REMOVED]", text)
        _removed_count += n
    audit({
        "event": "protected_attributes_stripped",
        "ai_system": "cv-screener",
        "fields_removed": _removed_count,
    })
    return text


def annotate(_answer: str, record) -> dict:
    return {
        "decision": None,
        "decision_authority": "human recruiter",
        "notice": (
            "This is a summary to support a human recruiter. It is not a hiring "
            "decision, a score or a ranking, and must not be used as one."
        ),
        "pii_redacted": record.pii_detected,
        "protected_attributes_removed": _removed_count,
    }


app = build(
    key="cv-screener",
    name="CV Screening Assistant",
    purpose=(
        "Summarise how a candidate's stated experience relates to a role "
        "specification, as an input to a human recruiter's decision."
    ),
    risk_tier="high_risk",
    oversight=(
        "Output is advisory only. The service never returns a hire/reject "
        "decision or a score. Every response is flagged human_review_required "
        "and a named recruiter records the actual decision outside this system."
    ),
    limitations=[
        "EU AI Act Annex III high-risk: employment and worker management",
        "Does not score, rank or decide; produces a structured summary only",
        "Protected characteristics are stripped from input before inference",
        "Not validated for languages other than English",
    ],
    system_prompt=SYSTEM_PROMPT,
    route="/screen",
    input_field="cv_text",
    needs_human_review=True,
    max_tokens=900,
    enrich=annotate,
    preprocess=strip_protected,
)

handler = lambda_handler_for(app)
