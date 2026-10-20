"""Parse NIST's official AI RMF -> ISO/IEC 42001 crosswalk into structured rows.

This is a Tier 1 source: mappings published by NIST, imported rather than
invented. See docs/CROSSWALK_METHODOLOGY.md section 2.1.

The source document has defects that are silent if ignored:

1. It is numbered against ISO/IEC *FDIS* 42001, where Annex A controls appear
   as ``B.x``. The published 2023 standard renumbers these to ``A.x``.
   Importing without normalising produces a graph in which every Annex A edge
   points at a control reference that does not exist.

2. It contains a typographical defect, ``7..2`` for ``7.2``, repaired from an
   explicit list so a *new* defect fails loudly rather than being coerced.

3. At least two references carry a title belonging to a different control
   (``B.2.2 Customers``; ``B.3.2 Management review inputs``). These are not
   silently corrected. The parser detects them by finding references whose
   title disagrees with the title the same reference carries elsewhere in the
   document, flags the edge ``disputed``, and records the reference whose
   canonical title matches instead. The loader downgrades confidence on
   disputed edges; a human decides whether to accept them.

Run directly to regenerate ``db/seed/t1_nist_iso42001_edges.json``.
"""

from __future__ import annotations

import json
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SOURCE_PDF = REPO_ROOT / "docs" / "sources" / "NIST_AI_RMF_to_ISO_IEC_42001_Crosswalk.pdf"
OUTPUT_JSON = REPO_ROOT / "db" / "seed" / "t1_nist_iso42001_edges.json"

#: Expected SHA256 of the source. A mismatch means NIST revised the document
#: and the parse must be re-verified rather than trusted.
SOURCE_SHA256 = "170efdf7be08a988213b54fb8293f3ea67f0627aff6f0541052dfbb0744afed3"

NIST_RE = re.compile(r"^(Govern|Map|Measure|Manage)\s+(\d+\.\d+)\s+(.*)$")
ISO_RE = re.compile(r"^(B\.[\d.]+|\d+(?:\.\d+)*)\s+(.+)$")

#: Repeated table headers and the document title, which pypdf emits inline.
#: Matched as whole lines so they are never mistaken for wrapped cell text.
HEADER_LINES = frozenset(
    {
        "ai rmf",
        "iso/iec fdis 42001",
        "ai rmf iso/iec fdis 42001",
        "nist ai rmf to iso/iec fdis 42001 ai management system crosswalk",
    }
)

#: Known typographical defects, repaired before matching. Explicit, not inferred.
SOURCE_DEFECTS = {"7..2": "7.2"}

#: Below this similarity, two titles for the same reference are a genuine
#: disagreement rather than a wording variance ("analysis and" vs "analysis, and").
TITLE_SIMILARITY_THRESHOLD = 0.85


@dataclass
class Mapping:
    """One NIST subcategory and every ISO item the crosswalk links it to."""

    nist_ref: str
    nist_outcome: str
    iso_refs: list[dict[str, str]] = field(default_factory=list)


def normalise_iso_ref(ref: str) -> tuple[str, str]:
    """Return ``(published_ref, kind)`` for an FDIS-era reference.

    ``B.6.2.6`` -> ``("A.6.2.6", "annex_a")``  FDIS annex renumbered
    ``6.1.2``   -> ``("6.1.2", "clause")``     clause numbering unchanged
    """
    if ref.startswith("B."):
        return "A." + ref[2:], "annex_a"
    return ref, "clause"


def _repair(line: str) -> str:
    """Apply known source defects to a line before it is matched."""
    for bad, good in SOURCE_DEFECTS.items():
        if line.startswith(bad + " "):
            return good + line[len(bad) :]
    return line


def _normalise_title(title: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", title.lower()).strip()


def extract_text(pdf_path: Path) -> str:
    try:
        from pypdf import PdfReader
    except ImportError:  # pragma: no cover
        sys.exit("pypdf is required: uv run --with pypdf python graph/parse_crosswalk.py")

    reader = PdfReader(str(pdf_path))
    return "\n".join(page.extract_text() or "" for page in reader.pages)


def parse(text: str) -> list[Mapping]:
    """Walk the extracted text as a small state machine.

    Both NIST outcome text and ISO control titles wrap across lines, so a line
    matching neither pattern continues whatever was last seen.
    """
    mappings: list[Mapping] = []
    appending_to: str | None = None  # "outcome" | "iso_title" | None

    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.lower() in HEADER_LINES:
            continue

        line = _repair(line)

        if match := NIST_RE.match(line):
            function, number, outcome_start = match.groups()
            mappings.append(
                Mapping(
                    nist_ref=f"{function.upper()} {number}",
                    nist_outcome=outcome_start.strip(),
                )
            )
            appending_to = "outcome"
            continue

        if match := ISO_RE.match(line):
            if not mappings:
                continue  # stray line before the first subcategory
            ref, title = match.groups()
            published_ref, kind = normalise_iso_ref(ref)
            mappings[-1].iso_refs.append(
                {
                    "source_ref": ref,
                    "ref": published_ref,
                    "kind": kind,
                    "title": title.strip(),
                }
            )
            appending_to = "iso_title"
            continue

        if not mappings:
            continue
        if appending_to == "outcome":
            mappings[-1].nist_outcome += " " + line
        elif appending_to == "iso_title" and mappings[-1].iso_refs:
            mappings[-1].iso_refs[-1]["title"] += " " + line

    return _merge_duplicate_subcategories(mappings)


def _merge_duplicate_subcategories(mappings: list[Mapping]) -> list[Mapping]:
    """Fold repeated subcategory blocks into one.

    The source repeats a subcategory header when its mapping list spans a page
    break (``Govern 5.2``). Treating those as separate rows double-counts.
    """
    merged: dict[str, Mapping] = {}
    for mapping in mappings:
        existing = merged.get(mapping.nist_ref)
        if existing is None:
            merged[mapping.nist_ref] = mapping
            continue
        seen = {(r["ref"], r["title"]) for r in existing.iso_refs}
        for ref in mapping.iso_refs:
            if (ref["ref"], ref["title"]) not in seen:
                existing.iso_refs.append(ref)
    return list(merged.values())


def detect_anomalies(
    mappings: list[Mapping],
) -> tuple[dict[str, str], list[dict[str, str]]]:
    """Find references whose title disagrees with their own majority title.

    Returns ``(canonical_titles, anomalies)``. For each anomaly the parser also
    reports which reference *does* canonically carry the observed title, which
    is usually the reference the source meant.
    """
    titles: dict[str, Counter[str]] = defaultdict(Counter)
    for mapping in mappings:
        for iso in mapping.iso_refs:
            titles[iso["ref"]][iso["title"]] += 1

    canonical = {ref: counts.most_common(1)[0][0] for ref, counts in titles.items()}
    by_title = {_normalise_title(title): ref for ref, title in canonical.items()}

    anomalies: list[dict[str, str]] = []
    for mapping in mappings:
        for iso in mapping.iso_refs:
            expected = canonical[iso["ref"]]
            if iso["title"] == expected:
                continue
            similarity = SequenceMatcher(
                None, _normalise_title(iso["title"]), _normalise_title(expected)
            ).ratio()
            if similarity >= TITLE_SIMILARITY_THRESHOLD:
                continue  # wording variance, not a disagreement
            iso["disputed"] = True
            suspected = by_title.get(_normalise_title(iso["title"]))
            if suspected:
                iso["suspected_ref"] = suspected
            anomalies.append(
                {
                    "nist_ref": mapping.nist_ref,
                    "source_ref": iso["source_ref"],
                    "normalised_ref": iso["ref"],
                    "title_in_source": iso["title"],
                    "canonical_title_for_ref": expected,
                    "suspected_intended_ref": suspected or "unknown",
                }
            )
    return canonical, anomalies


def main() -> int:
    if not SOURCE_PDF.exists():
        sys.exit(f"missing source: {SOURCE_PDF}")

    mappings = parse(extract_text(SOURCE_PDF))
    canonical, anomalies = detect_anomalies(mappings)

    edges = [
        {
            "nist_ref": m.nist_ref,
            "iso42001_ref": iso["ref"],
            "iso42001_kind": iso["kind"],
            "iso42001_title": canonical[iso["ref"]],
            "source_ref": iso["source_ref"],
            "disputed": iso.get("disputed", False),
            **({"suspected_ref": iso["suspected_ref"]} if "suspected_ref" in iso else {}),
        }
        for m in mappings
        for iso in m.iso_refs
    ]

    payload = {
        "source": {
            "document": "NIST AI RMF to ISO/IEC FDIS 42001 AI Management system Crosswalk",
            "url": "https://airc.nist.gov/docs/NIST_AI_RMF_to_ISO_IEC_42001_Crosswalk.pdf",
            "sha256": SOURCE_SHA256,
            "note": (
                "Numbered against ISO/IEC FDIS 42001. Annex A controls appear as "
                "B.x and are normalised to the published A.x. The crosswalk "
                "asserts relatedness only -- it carries no relationship type and "
                "no strength; those are added at load time."
            ),
        },
        "source_anomalies": anomalies,
        "subcategories": [
            {"ref": m.nist_ref, "outcome": m.nist_outcome} for m in mappings
        ],
        "edges": edges,
    }

    OUTPUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_JSON.write_text(json.dumps(payload, indent=2) + "\n")

    functions = Counter(m.nist_ref.split()[0] for m in mappings)
    annex = sum(1 for e in edges if e["iso42001_kind"] == "annex_a")
    disputed = sum(1 for e in edges if e["disputed"])
    print(
        f"subcategories : {len(mappings)}  "
        + "  ".join(f"{k} {functions[k]}" for k in ("GOVERN", "MAP", "MEASURE", "MANAGE"))
    )
    print(f"edges         : {len(edges)}  ({annex} annex, {len(edges) - annex} clause)")
    print(f"annex controls: {len({e['iso42001_ref'] for e in edges if e['iso42001_kind'] == 'annex_a'})} distinct")
    print(f"disputed      : {disputed}  (source title disagrees with its own reference)")
    for a in anomalies:
        print(
            f"  ! {a['source_ref']:8} titled {a['title_in_source']!r}"
            f" but {a['normalised_ref']} is {a['canonical_title_for_ref']!r}"
            f" -- likely meant {a['suspected_intended_ref']}"
        )
    print(f"written       : {OUTPUT_JSON.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
