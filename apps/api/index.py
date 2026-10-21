"""Vercel entry point: the three governed AI systems, as one deployment.

Each application keeps its own base path, system card, risk tier and
behaviour, so the estate is still three distinct systems:

    /support-chat   limited risk, conversational
    /cv-screener    EU AI Act Annex III high-risk, employment screening
    /doc-rag        limited risk, retrieval over a policy corpus

They share a deployment rather than a codebase because they already share the
runtime in `apps/common`. Nothing about the governance model depends on that:
the collector probes each base URL separately and records evidence against each
system separately.

Deployed with `apps/` as the project root, so this shim registers an `apps`
package alias pointing at that root. Without it the `from apps.common...`
imports used everywhere else would only resolve when running from the repo
root, and the same source would not run in both places.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

APPS_ROOT = Path(__file__).resolve().parent.parent

if str(APPS_ROOT) not in sys.path:
    sys.path.insert(0, str(APPS_ROOT))

# make `apps.common...` resolve when `apps/` itself is the deployment root
if "apps" not in sys.modules:
    alias = types.ModuleType("apps")
    alias.__path__ = [str(APPS_ROOT)]  # type: ignore[attr-defined]
    sys.modules["apps"] = alias

from fastapi import FastAPI, Request  # noqa: E402
from fastapi.responses import JSONResponse  # noqa: E402

from apps.cv_screener.main import app as cv_screener  # noqa: E402
from apps.doc_rag.main import app as doc_rag  # noqa: E402
from apps.support_chat.main import app as support_chat  # noqa: E402

SYSTEMS = {
    "support-chat": support_chat,
    "cv-screener": cv_screener,
    "doc-rag": doc_rag,
}

app = FastAPI(title="Governed AI estate", version="1.0.0")

for prefix, sub in SYSTEMS.items():
    app.mount(f"/{prefix}", sub)


@app.get("/")
def index() -> JSONResponse:
    """An index of the estate, so the deployment is self-describing."""
    return JSONResponse(
        {
            "estate": "ai-control-graph demo estate",
            "note": (
                "Three AI systems under governance. Each serves its own system "
                "card and is probed separately by the evidence collector."
            ),
            "systems": {
                key: {
                    "base": f"/{key}",
                    "card": f"/{key}/.well-known/ai-system-card.json",
                    "health": f"/{key}/healthz",
                }
                for key in SYSTEMS
            },
            "graph": "https://ai-control-graph.vercel.app",
            "repo": "https://github.com/VIAMRSH123/ai-control-graph",
        }
    )


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok", "systems": list(SYSTEMS)}


@app.api_route("/{full_path:path}", methods=["GET", "POST"])
def unmatched(full_path: str, request: Request) -> JSONResponse:
    """Report what path actually arrived, rather than a bare 404.

    The platform rewrite can replace the request path before the ASGI app sees
    it, which turns every route into a silent 404. Echoing the observed path
    makes that visible instead of mysterious.
    """
    return JSONResponse(
        status_code=404,
        content={
            "detail": "no route matched",
            "observed_path": request.url.path,
            "forwarded": {
                k: v for k, v in request.headers.items()
                if k.lower().startswith(("x-vercel", "x-forwarded"))
            },
            "systems": list(SYSTEMS),
        },
    )
