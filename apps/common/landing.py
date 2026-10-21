"""A human-readable index for the estate.

The root of a deployment is the first thing a person sees. Returning raw JSON
there is fine for a machine and useless for a reader, so the root serves this
page and the same information stays available as JSON at `/index.json` or to
any client that asks for it via `Accept`.

Visual language matches the crosswalk UI deliberately: the two deployments are
halves of one system, and looking like it costs nothing.
"""

from __future__ import annotations

from html import escape
from typing import Any

#: Risk tier → (label, colour token). Colour carries meaning, not decoration.
TIER = {
    "high_risk": ("EU AI Act Annex III · high risk", "#f0b429"),
    "limited": ("Limited risk", "#2dd4bf"),
    "gpai": ("General-purpose AI", "#a78bfa"),
}

STYLE = """
*{box-sizing:border-box}
body{margin:0;background:#09090b;color:#fafafa;
  font-family:Inter,-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
  font-size:14px;line-height:1.55;-webkit-font-smoothing:antialiased}
.wrap{max-width:760px;margin:0 auto;padding:56px 16px 72px}
h1{font-size:20px;font-weight:600;letter-spacing:-.01em;margin:0 0 6px}
.sub{color:#a1a1aa;margin:0 0 8px}
.lede{color:#6b6b76;font-size:13px;margin:0 0 36px;max-width:60ch}
.card{border:1px solid #26262b;border-radius:10px;padding:16px 18px;margin-bottom:12px;
  background:#121214}
.head{display:flex;align-items:baseline;gap:10px;flex-wrap:wrap;margin-bottom:4px}
.name{font-weight:600}
.key{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:12px;color:#6b6b76}
.tier{margin-left:auto;font-size:11px;padding:2px 8px;border-radius:999px;
  border:1px solid currentColor;white-space:nowrap}
.purpose{color:#a1a1aa;font-size:13px;margin:0 0 12px}
.links{display:flex;flex-wrap:wrap;gap:8px}
a.pill{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:11.5px;
  color:#a1a1aa;text-decoration:none;border:1px solid #35353c;border-radius:6px;
  padding:3px 9px;transition:border-color .14s,color .14s}
a.pill:hover{color:#fafafa;border-color:#5b8def}
footer{margin-top:36px;padding-top:20px;border-top:1px solid #26262b;
  display:flex;gap:18px;flex-wrap:wrap;font-size:13px}
footer a{color:#5b8def;text-decoration:none}
footer a:hover{text-decoration:underline}
.note{color:#6b6b76;font-size:12px;margin-top:22px;max-width:62ch}
@media(prefers-reduced-motion:reduce){*{transition-duration:1ms!important}}
"""


def render(systems: dict[str, Any], graph_url: str, repo_url: str) -> str:
    """Build the index page. `systems` maps base path → system card dict."""
    cards = []
    for base, card in systems.items():
        label, colour = TIER.get(card.get("risk_tier", ""), ("Unclassified", "#6b6b76"))
        route = (card.get("interaction") or {}).get("route", "/")
        cards.append(
            f"""
    <section class="card">
      <div class="head">
        <span class="name">{escape(card.get("name", base))}</span>
        <span class="key">{escape(base)}</span>
        <span class="tier" style="color:{colour}">{escape(label)}</span>
      </div>
      <p class="purpose">{escape(card.get("purpose", ""))}</p>
      <div class="links">
        <a class="pill" href="{escape(base)}/.well-known/ai-system-card.json">system card</a>
        <a class="pill" href="{escape(base)}/healthz">health</a>
        <span class="pill">POST {escape(base)}{escape(route)}</span>
      </div>
    </section>"""
        )

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Governed AI estate — AI Control Graph</title>
<meta name="description" content="Three AI systems under governance, probed for compliance evidence that propagates across ISO 27001, ISO 42001, the EU AI Act and NIST AI RMF.">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600&display=swap" rel="stylesheet">
<style>{STYLE}</style>
</head>
<body>
<main class="wrap">
  <h1>Governed AI estate</h1>
  <p class="sub">Three AI systems under governance</p>
  <p class="lede">
    These are real deployed services, not mocks. An evidence collector probes each
    one over HTTPS — fetching its system card, checking AI-interaction disclosure
    and rate limiting, and firing a genuine prompt-injection attempt to record how
    it responds. Each artifact is tagged to one control and propagates across all
    four frameworks in the crosswalk.
  </p>
{''.join(cards)}
  <p class="note">
    Every response carries an AI disclosure. Prompts are never logged in full —
    only a SHA-256 digest, with personal data redacted before anything is written.
    The screener never returns a hiring decision or a score.
  </p>
  <footer>
    <a href="{escape(graph_url)}">→ The crosswalk</a>
    <a href="{escape(repo_url)}">→ Source</a>
    <a href="/index.json">→ This page as JSON</a>
  </footer>
</main>
</body>
</html>"""
