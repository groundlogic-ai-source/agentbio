"""
PubMed literature retrieval via NCBI E-utilities
(https://eutils.ncbi.nlm.nih.gov/entrez/eutils/).

Two-step retrieval (esearch -> efetch) followed by a per-abstract LLM relevance
gate: each abstract is screened with a constrained claude-sonnet-4-6 call that
answers only YES/NO to "does this abstract assert a specific relationship?".
Abstracts that come back NO are discarded. Every kept abstract retains its PMID,
which is the provenance key used by the agents downstream.

NCBI_API_KEY (optional) raises the E-utilities rate limit from 3 to 10 req/s.
"""

import os
import time
import requests
import xml.etree.ElementTree as ET
from typing import Any, Optional

import anthropic

from cache.cache import get, set as cache_set, make_key

BASE_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/"
MODEL = "claude-sonnet-4-6"


def _anthropic_client() -> Optional[anthropic.Anthropic]:
    base_url = os.environ.get("AI_INTEGRATIONS_ANTHROPIC_BASE_URL")
    api_key = os.environ.get("AI_INTEGRATIONS_ANTHROPIC_API_KEY")
    if not base_url or not api_key:
        return None
    return anthropic.Anthropic(base_url=base_url, api_key=api_key)


def _api_key_params() -> dict[str, str]:
    key = os.environ.get("NCBI_API_KEY")
    return {"api_key": key} if key else {}


def _esearch(term: str, retmax: int) -> list[str]:
    params = {
        "db": "pubmed",
        "term": term,
        "retmax": retmax,
        "retmode": "json",
        "sort": "relevance",
        **_api_key_params(),
    }
    resp = requests.get(f"{BASE_URL}esearch.fcgi", params=params, timeout=30)
    resp.raise_for_status()
    return resp.json().get("esearchresult", {}).get("idlist", [])


def _efetch(pmids: list[str]) -> dict[str, str]:
    """Fetch abstracts for a list of PMIDs. Returns {pmid: abstract_text}."""
    if not pmids:
        return {}
    params = {
        "db": "pubmed",
        "id": ",".join(pmids),
        "retmode": "xml",
        "rettype": "abstract",
        **_api_key_params(),
    }
    resp = requests.get(f"{BASE_URL}efetch.fcgi", params=params, timeout=60)
    resp.raise_for_status()

    abstracts: dict[str, str] = {}
    root = ET.fromstring(resp.content)
    for article in root.findall(".//PubmedArticle"):
        pmid_el = article.find(".//PMID")
        if pmid_el is None or not pmid_el.text:
            continue
        pmid = pmid_el.text
        parts = [el.text or "" for el in article.findall(".//Abstract/AbstractText")]
        text = " ".join(p.strip() for p in parts if p).strip()
        if text:
            abstracts[pmid] = text
    return abstracts


def _llm_relationship(abstract: str, subject: str, obj: str,
                      client: anthropic.Anthropic) -> tuple[bool, str]:
    """
    Ask the model whether the abstract ASSERTS a specific relationship between
    `subject` and `obj`, versus merely co-mentioning them. Returns (kept, reason).
    """
    prompt = (
        f"Does this abstract assert a specific relationship between {subject} and "
        f"{obj}, or does it merely mention them in the same text without asserting "
        f"a connection? Answer only with YES or NO, followed by a one-sentence "
        f"reason.\n\nAbstract:\n{abstract[:4000]}"
    )
    try:
        msg = client.messages.create(
            model=MODEL,
            max_tokens=120,
            messages=[{"role": "user", "content": prompt}],
        )
        block = msg.content[0]
        text = (block.text if block.type == "text" else str(block)).strip()
        kept = text.upper().lstrip().startswith("YES")
        reason = text.split("\n", 1)[0].strip()
        return kept, reason
    except Exception as e:
        # On LLM failure we cannot assert a relationship — drop the abstract.
        return False, f"[relevance check failed: {e}]"


def search_literature(drug_name: Optional[str], target_name: str,
                      disease_name: str, retmax: int = 8) -> dict[str, Any]:
    """
    Search PubMed for literature connecting (drug,) target, and disease, then keep
    only abstracts whose relationship is LLM-confirmed.

    `drug_name` may be None (e.g. at the biologist stage, before any compound is
    chosen) — in that case the search/relationship is target<->disease.

    Returns:
      {
        query: str,
        n_screened: int,
        n_kept: int,
        literature_hits: [{pmid, summary, relationship_asserted: True, abstract}],
        error: str | None,
      }
    """
    cache_key = make_key("search_literature", drug_name, target_name, disease_name, retmax)
    cached = get(cache_key)
    if cached is not None:
        return cached

    if drug_name:
        subject = drug_name
        obj = f"{target_name} / {disease_name}"
        term = f'("{drug_name}") AND ("{target_name}" OR "{disease_name}")'
    else:
        subject = target_name
        obj = disease_name
        term = f'("{target_name}") AND ("{disease_name}")'

    result: dict[str, Any] = {
        "query": term,
        "n_screened": 0,
        "n_kept": 0,
        "literature_hits": [],
        "error": None,
    }

    client = _anthropic_client()
    if client is None:
        result["error"] = "AI integration not configured; cannot verify relationships"
        print("[pubmed] WARNING: no Anthropic client; literature relationship gate skipped")
        cache_set(cache_key, result, ttl_days=1)
        return result

    try:
        pmids = _esearch(term, retmax)
        abstracts = _efetch(pmids)
        result["n_screened"] = len(abstracts)

        hits = []
        for pmid, abstract in abstracts.items():
            kept, reason = _llm_relationship(abstract, subject, obj, client)
            if kept:
                hits.append({
                    "pmid": pmid,
                    "summary": reason,
                    "relationship_asserted": True,
                    "abstract": abstract[:1000],
                })
            time.sleep(0.12)  # stay under E-utilities rate limit

        result["literature_hits"] = hits
        result["n_kept"] = len(hits)
    except Exception as e:
        result["error"] = str(e)
        print(f"[pubmed] WARNING: literature search failed for '{term}': {e}")

    cache_set(cache_key, result, ttl_days=7)
    return result
