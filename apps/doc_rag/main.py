"""Policy document assistant — retrieval over an internal corpus.

Exercises the data-governance half of the graph: corpus provenance, retrieval
logging and groundedness. Answers are constrained to retrieved passages, and
the passages used are returned with the answer so a reader can check it.
"""

import hashlib
import json
import os
from pathlib import Path

from apps.common.core import audit
from apps.common.service import build, lambda_handler_for

CORPUS_PATH = Path(os.environ.get("CORPUS_PATH", Path(__file__).parent / "corpus.json"))

SYSTEM_PROMPT = """You answer questions about internal policy using only the \
passages provided.

Quote or closely paraphrase the passages. Cite each passage you use by its id.

If the passages do not contain the answer, say exactly that and say what is \
missing. Do not supply general knowledge as if it came from the policy. An \
unanswerable question answered confidently is worse than one left open."""


def _corpus() -> list[dict]:
    if CORPUS_PATH.exists():
        return json.loads(CORPUS_PATH.read_text())["documents"]
    return []


def retrieve(question: str, k: int = 3) -> list[dict]:
    """Keyword overlap retrieval.

    Deliberately simple and deliberately logged. What matters for the controls
    this app evidences is that retrieval is recorded and that answers are
    constrained to what was retrieved, not which ranking function was used.
    """
    terms = {w for w in question.lower().split() if len(w) > 3}
    scored = []
    for doc in _corpus():
        text = f"{doc['title']} {doc['text']}".lower()
        overlap = sum(1 for term in terms if term in text)
        if overlap:
            scored.append((overlap, doc))
    scored.sort(key=lambda pair: pair[0], reverse=True)
    hits = [doc for _, doc in scored[:k]]
    audit({
        "event": "retrieval",
        "ai_system": "doc-rag",
        "question_sha256": hashlib.sha256(question.encode()).hexdigest(),
        "documents_retrieved": [d["id"] for d in hits],
        "corpus_size": len(_corpus()),
    })
    return hits


_last_hits: list[dict] = []


def with_context(question: str) -> str:
    global _last_hits
    _last_hits = retrieve(question)
    if not _last_hits:
        return f"PASSAGES\n(none matched)\n\nQUESTION\n{question}"
    passages = "\n\n".join(f"[{d['id']}] {d['title']}\n{d['text']}" for d in _last_hits)
    return f"PASSAGES\n{passages}\n\nQUESTION\n{question}"


def cite(_answer: str, _record) -> dict:
    return {
        "sources": [{"id": d["id"], "title": d["title"], "provenance": d.get("provenance")}
                    for d in _last_hits],
        "grounded": bool(_last_hits),
    }


app = build(
    key="doc-rag",
    name="Policy Document Assistant",
    purpose="Answer questions about internal policy, grounded in a retrieved corpus.",
    risk_tier="limited",
    oversight=(
        "Every answer returns the passages it was built from, so a reader can "
        "verify it. Answers are refused when retrieval returns nothing."
    ),
    limitations=[
        "Answers only from the indexed corpus; no general knowledge",
        "Keyword retrieval, not semantic: phrasing affects recall",
        "Corpus is a snapshot; see each source's provenance for its date",
    ],
    system_prompt=SYSTEM_PROMPT,
    route="/ask",
    input_field="question",
    enrich=cite,
    preprocess=with_context,
)

handler = lambda_handler_for(app)
