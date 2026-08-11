"""Step-1 gate for the triage discrimination benchmark: does disease-side
holdout redaction actually block indication leakage through the AUDIT lanes?

The discovery benchmark's holdout (``data_sources/holdout.py``) seals
disease-side evidence on the DISCOVERY side — Open Targets approved-drug
name lists, the ChEMBL ``drug_indication`` EFO fallback, and the
``has_approved``/unmet-need signal they feed.  The audit layer is a
different code path with different sources.  Before any discrimination
study can be built on audit output, we must show that a confirmed
repurposing is not trivially identifiable from the audit envelope itself.

This probe is measurement only.  It calls the production audit-context
builder under an ACTIVE holdout for the confirmed drug and asks one
mechanical question per lane:

    does the returned envelope contain the held-out drug's approved
    indication, in text a downstream evidence profile could read?

Leakage is detected by token matching against the disease name (plus
registered abbreviations), never by judgment.  A hit is reported with the
field and a bounded quote excerpt so the finding is checkable by hand.

Usage:
    python3 -m validation.audit_lane_holdout_probe
    python3 -m validation.audit_lane_holdout_probe --json out.json
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from typing import Any, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from data_sources import holdout as holdout_mod  # noqa: E402

# Confirmed drug→disease pairs. Every one is an approved indication, so the
# ground truth of "this drug treats this disease" is not in question — the
# only question is whether the audit envelope states it.
#
# ``aliases`` are additional surface forms a label may use for the same
# indication (abbreviations, older nomenclature). They make the probe
# STRICTER, never more lenient: they can only add leak detections.
PROBE_PAIRS: list[dict[str, Any]] = [
    {
        "drug": "Sildenafil",
        "disease": "Pulmonary arterial hypertension",
        "mechanism_symbol": "PDE5A",
        "aliases": ["pulmonary hypertension", "PAH"],
    },
    {
        "drug": "Thalidomide",
        "disease": "Multiple myeloma",
        "mechanism_symbol": "CRBN",
        "aliases": ["myeloma"],
    },
    {
        "drug": "Tretinoin",
        "disease": "Acute promyelocytic leukemia",
        "mechanism_symbol": "RARA",
        "aliases": ["promyelocytic", "APL"],
    },
    {
        "drug": "Everolimus",
        "disease": "Tuberous sclerosis complex",
        "mechanism_symbol": "MTOR",
        "aliases": ["tuberous sclerosis", "TSC"],
    },
    {
        "drug": "Anakinra",
        "disease": "Cryopyrin-associated periodic syndromes",
        "mechanism_symbol": "IL1R1",
        "aliases": ["cryopyrin", "CAPS", "NOMID",
                    "neonatal-onset multisystem inflammatory disease"],
    },
]

# Tokens too generic to prove that a specific indication leaked.
_STOPWORDS = {
    "acute", "chronic", "disease", "diseases", "syndrome", "syndromes",
    "disorder", "disorders", "complex", "type", "deficiency", "primary",
    "secondary", "associated", "periodic", "multiple", "arterial",
    "pulmonary", "and", "the", "with", "of",
}

_LANES = ("regulatory_label", "entity_linked_literature")


def _significant_tokens(disease: str) -> list[str]:
    """Disease tokens specific enough that their presence indicates the
    indication itself, not incidental vocabulary."""
    raw = re.split(r"[^A-Za-z0-9]+", disease.lower())
    return [t for t in raw if len(t) >= 5 and t not in _STOPWORDS]


def _needles(pair: dict[str, Any]) -> list[str]:
    """Every surface form whose presence counts as an indication leak."""
    needles = {pair["disease"].lower()}
    needles.update(a.lower() for a in pair.get("aliases") or [])
    needles.update(_significant_tokens(pair["disease"]))
    return sorted(n for n in needles if n)


def _iter_text(node: Any, path: str = "") -> list[tuple[str, str]]:
    """Every string leaf in the envelope, with its dotted path."""
    out: list[tuple[str, str]] = []
    if isinstance(node, str):
        out.append((path, node))
    elif isinstance(node, dict):
        for key, value in node.items():
            out.extend(_iter_text(value, f"{path}.{key}" if path else str(key)))
    elif isinstance(node, list):
        for index, value in enumerate(node):
            out.extend(_iter_text(value, f"{path}[{index}]"))
    return out


def _find_leaks(node: Any, needles: list[str]) -> list[dict[str, Any]]:
    """Mechanical substring search. A word-boundary match for alphabetic
    needles avoids counting 'caps' inside 'capsule'."""
    leaks: list[dict[str, Any]] = []
    for path, text in _iter_text(node):
        low = text.lower()
        for needle in needles:
            if needle.isalpha():
                found = re.search(rf"\b{re.escape(needle)}\b", low)
            else:
                found = re.search(re.escape(needle), low)
            if not found:
                continue
            start = max(0, found.start() - 60)
            leaks.append({
                "path": path,
                "needle": needle,
                "excerpt": text[start:found.end() + 60].replace("\n", " "),
            })
            break  # one leak record per text leaf is enough to prove it
    return leaks


def _raw_lanes(drug: str, mechanism_symbol: str) -> dict[str, Any]:
    """The lane payloads exactly as `build_audit_context` fetches them,
    with the redaction step skipped.

    This reproduces the pre-fix state on demand, so the finding that the
    audit lanes leaked the indication stays verifiable rather than
    becoming a claim about code that no longer exists.
    """
    from data_sources.openfda import get_label_evidence
    from data_sources.pubtator_assertions import (
        search_drug_mechanism_assertions)

    return {
        "sources": {
            "regulatory_label": get_label_evidence(drug),
            "entity_linked_literature": (
                search_drug_mechanism_assertions(drug, mechanism_symbol)
                if mechanism_symbol
                else {"provider": "pubtator3", "status": "empty",
                      "assertions": []}),
        },
        "findings": [],
    }


def probe_pair(pair: dict[str, Any],
               *, unredacted: bool = False) -> dict[str, Any]:
    """Build the production audit context for one confirmed pair with the
    drug held out, then search each lane for the approved indication."""
    from api.audit_context import build_audit_context

    needles = _needles(pair)
    with holdout_mod.holdout_active([pair["drug"]]):
        holdout_active_during_call = holdout_mod.is_active()
        if unredacted:
            context = _raw_lanes(pair["drug"], pair["mechanism_symbol"])
        else:
            context = build_audit_context(
                pair["drug"], mechanism_symbol=pair["mechanism_symbol"])

    sources = (context or {}).get("sources") or {}
    per_lane: dict[str, Any] = {}
    for lane in _LANES:
        payload = sources.get(lane) or {}
        leaks = _find_leaks(payload, needles)
        per_lane[lane] = {
            "status": payload.get("status"),
            "leaked": bool(leaks),
            "leak_count": len(leaks),
            "leak_paths": sorted({leak["path"] for leak in leaks})[:12],
            "sample_leaks": leaks[:3],
        }

    findings_leaks = _find_leaks((context or {}).get("findings") or [], needles)
    return {
        "drug": pair["drug"],
        "disease": pair["disease"],
        "mechanism_symbol": pair["mechanism_symbol"],
        "holdout_active_during_call": holdout_active_during_call,
        "needles": needles,
        "lanes": per_lane,
        "findings_leaked": bool(findings_leaks),
        "findings_leak_count": len(findings_leaks),
        "any_leak": any(per_lane[lane]["leaked"] for lane in _LANES)
                    or bool(findings_leaks),
    }


def _lane_hooks_holdout() -> dict[str, bool]:
    """Static check: does either audit-lane source module consult the
    holdout module at all?  A lane that never imports holdout cannot be
    redacted by it, whatever the runtime result happens to show."""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    out: dict[str, bool] = {}
    for module in ("data_sources/openfda.py",
                   "data_sources/pubtator_assertions.py"):
        path = os.path.join(root, module)
        try:
            with open(path, encoding="utf-8") as handle:
                out[module] = "holdout" in handle.read()
        except OSError:
            out[module] = False
    return out


def build_report(results: list[dict[str, Any]],
                 hooks: dict[str, bool]) -> str:
    leaking = [r for r in results if r["any_leak"]]
    lines = [
        "# Audit-lane holdout probe — does disease-side redaction reach the "
        "audit layer?",
        "",
        "Measurement only. For each confirmed drug→disease pair the "
        "production audit context is built with the drug held out, then each "
        "lane is searched for the approved indication.",
        "",
        "## Static check: do the audit-lane sources consult holdout at all?",
        "",
        "| Source module | References `holdout` |",
        "| --- | --- |",
    ]
    for module, hooked in sorted(hooks.items()):
        lines.append(f"| `{module}` | {'yes' if hooked else '**no**'} |")
    lines += [
        "",
        "## Runtime probe",
        "",
        "| Drug | Disease | Regulatory lane | Literature lane | Findings | "
        "Indication leaked |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for r in results:
        reg = r["lanes"]["regulatory_label"]
        lit = r["lanes"]["entity_linked_literature"]
        lines.append(
            f"| {r['drug']} | {r['disease']} | "
            f"{reg['status']} ({reg['leak_count']} leaks) | "
            f"{lit['status']} ({lit['leak_count']} leaks) | "
            f"{r['findings_leak_count']} leaks | "
            f"{'**YES**' if r['any_leak'] else 'no'} |"
        )
    lines += [
        "",
        f"**{len(leaking)}/{len(results)} pairs leaked the approved "
        f"indication into the audit envelope under an active holdout.**",
        "",
        "## Sample leaked text",
        "",
    ]
    for r in results:
        if not r["any_leak"]:
            continue
        lines.append(f"### {r['drug']} / {r['disease']}")
        lines.append("")
        for lane in _LANES:
            for leak in r["lanes"][lane]["sample_leaks"]:
                excerpt = leak["excerpt"].strip()
                lines.append(
                    f"- `{lane}` · `{leak['path']}` · matched "
                    f"`{leak['needle']}` — …{excerpt}…")
        lines.append("")
    return "\n".join(lines)


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Probe whether holdout redaction reaches the audit lanes.")
    parser.add_argument("--json", default=None, help="write raw results JSON")
    parser.add_argument("--markdown", default=None, help="write the report")
    parser.add_argument(
        "--unredacted", action="store_true",
        help="skip the redaction step and probe the raw lanes, reproducing "
             "the pre-fix state that motivated this gate")
    args = parser.parse_args(argv)

    hooks = _lane_hooks_holdout()
    results = []
    for pair in PROBE_PAIRS:
        print(f"[probe] {pair['drug']} / {pair['disease']}", flush=True)
        result = probe_pair(pair, unredacted=args.unredacted)
        verdict = "LEAK" if result["any_leak"] else "clean"
        print(f"[probe]   → {verdict} "
              f"(regulatory={result['lanes']['regulatory_label']['status']}, "
              f"literature="
              f"{result['lanes']['entity_linked_literature']['status']})",
              flush=True)
        results.append(result)

    report = build_report(results, hooks)
    if args.json:
        with open(args.json, "w", encoding="utf-8") as handle:
            json.dump({"lane_hooks": hooks, "results": results}, handle,
                      indent=2)
    if args.markdown:
        with open(args.markdown, "w", encoding="utf-8") as handle:
            handle.write(report)
    else:
        print(report)

    leaked = sum(1 for r in results if r["any_leak"])
    print(f"\n[probe] {leaked}/{len(results)} pairs leaked the indication.")
    return 1 if leaked else 0


if __name__ == "__main__":
    raise SystemExit(main())
