"""
UniProt protein sequence retrieval (Stage 3).

Fetches the canonical amino-acid sequence for a UniProt accession from the public
UniProt REST API (no key required). Cache-first like every other wrapper.

Why this exists: Boltz co-folding (data_sources/boltz_api.predict_complex) needs the
protein *sequence* as input. AFDB (afdb.py) gives us a structure + pLDDT for the
free (apo) protein, but not a ready-to-submit sequence and never a ligand complex,
so the sequence is fetched here and the complex is predicted by Boltz.
"""

import requests
from typing import Optional

from cache.cache import get, set as cache_set, make_key

FASTA_URL = "https://rest.uniprot.org/uniprotkb/{acc}.fasta"


def get_protein_sequence(uniprot_id: str) -> Optional[str]:
    """Return the canonical amino-acid sequence for a UniProt accession, or None."""
    if not uniprot_id:
        return None

    cache_key = make_key("get_protein_sequence", uniprot_id)
    cached = get(cache_key)
    if cached is not None:
        return cached or None

    seq: Optional[str] = None
    try:
        resp = requests.get(FASTA_URL.format(acc=uniprot_id), timeout=30)
        if resp.status_code == 200:
            lines = resp.text.splitlines()
            seq = "".join(ln.strip() for ln in lines if ln and not ln.startswith(">"))
    except Exception as e:
        print(f"[uniprot] WARNING: sequence fetch failed for '{uniprot_id}': {e}")

    # Cache empty string on miss so we don't hammer the API; return None to caller.
    cache_set(cache_key, seq or "", ttl_days=30)
    return seq or None
