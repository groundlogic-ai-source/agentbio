"""
Boltz API integration (https://api.boltz.bio) — Stage 3.

Two wrappers over the official `boltz-api` Python SDK:

  predict_complex(protein_sequence, ligand_smiles)
      Co-folds the protein + ligand and returns
      {structure_confidence, binding_pose_confidence, predicted_affinity,
       pdb_or_cif_url, ...}.

  predict_adme(smiles)
      Returns Boltz's Tier-1 ADME summary {lipophilicity, permeability,
      solubility} for a single SMILES.

Design notes
------------
- CACHE-FIRST: results are keyed on their inputs in the shared SQLite cache, so a
  repeat run NEVER re-spends. Unavailable results (no key / failure) are NOT cached.
- COST: every predict_complex call prints an estimated cost so spend is auditable.
- GRACEFUL DEGRADATION: if BOLTZ_API_KEY (or the SDK) is missing, the wrappers
  return a structured {available: False, ...} object with all metrics None — they
  never raise — so the rest of the pipeline can still run and the report can note
  the gap.

What the numbers mean (carried into the Writer's Limitations section)
--------------------------------------------------------------------
Boltz-2 reports CONFIDENCE / PROBABILITY / relative optimization scores, all on a
0-1 scale, NOT an absolute Kd/IC50:
  - structure_confidence      : confidence in the predicted 3D structure (0-1)
  - binding_pose_confidence   : Boltz `binding_confidence` — confidence that binding
                                occurs, combining affinity probability with
                                structural quality (0.7+ = high confidence)
  - predicted_affinity        : Boltz `optimization_score` — a RELATIVE binding
                                strength ranking for lead optimisation (0-1),
                                NOT a measured affinity.
"""

import os
import time
import urllib.request
from typing import Any, Optional

from cache.cache import get, set as cache_set, make_key

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STRUCTURES_DIR = os.path.join(_REPO_ROOT, "output", "structures")

STRUCTURE_MODEL = "boltz-2.1"
ADME_MODEL = "adme-v1"

# Boltz docs: a prediction costs "as little as $0.025"; larger complexes / more
# samples cost more. Used only for the printed *estimate* — the real charge is
# metered by Boltz.
EST_COST_PER_SAMPLE_USD = 0.025

POLL_SECONDS = 5
COMPLEX_TIMEOUT_SECONDS = 1800   # 30 min ceiling for one co-folding job
ADME_TIMEOUT_SECONDS = 600


def _client() -> tuple[Optional[Any], Optional[str]]:
    api_key = os.environ.get("BOLTZ_API_KEY")
    if not api_key:
        return None, "BOLTZ_API_KEY not set"
    try:
        from boltz_api import Boltz
    except ImportError:
        return None, "boltz-api SDK not installed"
    try:
        return Boltz(api_key=api_key), None
    except Exception as e:  # pragma: no cover - defensive
        return None, f"Boltz client init failed: {e}"


def _to_dict(obj: Any) -> Any:
    """Coerce an SDK pydantic model (possibly nested) into plain dict/list/scalars."""
    if obj is None:
        return None
    if hasattr(obj, "model_dump"):
        try:
            return obj.model_dump()
        except Exception:
            pass
    if isinstance(obj, dict):
        return {k: _to_dict(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_to_dict(v) for v in obj]
    return obj


def _dig(d: Any, *path: str, default: Any = None) -> Any:
    cur = d
    for p in path:
        if isinstance(cur, dict):
            cur = cur.get(p)
        else:
            cur = getattr(cur, p, None)
        if cur is None:
            return default
    return cur


def predict_complex(protein_sequence: str, ligand_smiles: str,
                    num_samples: int = 1) -> dict[str, Any]:
    """
    Submit a protein-ligand co-folding job to Boltz, wait for it, and return the
    structure + binding metrics. See module docstring for the meaning of each field.
    """
    result: dict[str, Any] = {
        "available": False,
        "structure_confidence": None,
        "binding_pose_confidence": None,
        "predicted_affinity": None,
        "pdb_or_cif_url": None,
        "estimated_cost_usd": 0.0,
        "raw_metrics": {},
        "model": STRUCTURE_MODEL,
        "error": None,
    }

    if not protein_sequence or not ligand_smiles:
        result["error"] = "missing protein_sequence or ligand_smiles"
        return result

    cache_key = make_key("boltz_predict_complex", protein_sequence, ligand_smiles,
                         num_samples, STRUCTURE_MODEL)
    cached = get(cache_key)
    if cached is not None:
        print(f"[boltz] predict_complex CACHE HIT (no spend) "
              f"seq_len={len(protein_sequence)} smiles={ligand_smiles[:24]}")
        return cached

    client, err = _client()
    if client is None:
        result["error"] = err
        print(f"[boltz] predict_complex SKIPPED ({err}); returning unavailable result")
        return result  # do NOT cache an unavailable result

    est = round(EST_COST_PER_SAMPLE_USD * max(1, num_samples), 4)
    result["estimated_cost_usd"] = est
    print(f"[boltz] predict_complex SUBMIT model={STRUCTURE_MODEL} "
          f"seq_len={len(protein_sequence)} num_samples={num_samples} "
          f"ESTIMATED COST ~${est} (actual charge metered by Boltz)")

    body = {
        "entities": [
            {"type": "protein", "value": protein_sequence, "chain_ids": ["A"]},
            {"type": "ligand_smiles", "value": ligand_smiles, "chain_ids": ["B"]},
        ],
        "binding": {"type": "ligand_protein_binding", "binder_chain_id": "B"},
        "num_samples": num_samples,
    }

    try:
        pred = client.predictions.structure_and_binding.start(
            model=STRUCTURE_MODEL, input=body)
        pred_id = getattr(pred, "id", None) or _dig(_to_dict(pred), "id")
        status = getattr(pred, "status", None)
        deadline = time.time() + COMPLEX_TIMEOUT_SECONDS
        while status not in ("succeeded", "failed") and time.time() < deadline:
            time.sleep(POLL_SECONDS)
            pred = client.predictions.structure_and_binding.retrieve(pred_id)
            status = getattr(pred, "status", None)

        d = _to_dict(pred)
        status = _dig(d, "status")
        if status != "succeeded":
            result["error"] = _dig(d, "error", "message") or f"status={status}"
            print(f"[boltz] predict_complex did not succeed: {result['error']}")
            return result

        best = _dig(d, "output", "best_sample") or {}
        metrics = best.get("metrics", {}) if isinstance(best, dict) else {}
        binding = _dig(d, "output", "binding_metrics") or {}

        result["structure_confidence"] = metrics.get("structure_confidence")
        result["binding_pose_confidence"] = binding.get("binding_confidence")
        result["predicted_affinity"] = binding.get("optimization_score")
        s3_url = _dig(best, "structure", "url")
        result["pdb_or_cif_url"] = s3_url
        result["raw_metrics"] = {"structure_metrics": metrics, "binding_metrics": binding}
        result["available"] = True

        # Download the CIF file immediately while the S3 pre-signed URL is still
        # valid (30-min window). Store it locally so the report can link to our
        # own /api/structures/ endpoint instead of the expiring S3 URL.
        if s3_url:
            try:
                os.makedirs(STRUCTURES_DIR, exist_ok=True)
                cif_filename = f"{cache_key[:32]}.cif"
                cif_path = os.path.join(STRUCTURES_DIR, cif_filename)
                urllib.request.urlretrieve(s3_url, cif_path)
                result["local_cif_filename"] = cif_filename
                print(f"[boltz] CIF saved locally → {cif_filename}")
            except Exception as dl_err:
                print(f"[boltz] WARN: could not download CIF for local storage: {dl_err}")

    except Exception as e:
        result["error"] = str(e)
        print(f"[boltz] predict_complex FAILED: {e}")
        return result

    cache_set(cache_key, result, ttl_days=30)
    return result


def predict_adme(smiles: str) -> dict[str, Any]:
    """Return Boltz Tier-1 ADME {lipophilicity, permeability, solubility} for a SMILES."""
    result: dict[str, Any] = {
        "available": False,
        "lipophilicity": None,
        "permeability": None,
        "solubility": None,
        "model": ADME_MODEL,
        "error": None,
    }

    if not smiles:
        result["error"] = "missing smiles"
        return result

    cache_key = make_key("boltz_predict_adme", smiles, ADME_MODEL)
    cached = get(cache_key)
    if cached is not None:
        return cached

    client, err = _client()
    if client is None:
        result["error"] = err
        print(f"[boltz] predict_adme SKIPPED ({err})")
        return result

    body = {"molecules": [{"smiles": smiles, "id": "candidate"}]}
    try:
        pred = client.predictions.adme.start(model=ADME_MODEL, input=body)
        pred_id = getattr(pred, "id", None) or _dig(_to_dict(pred), "id")
        status = getattr(pred, "status", None)
        deadline = time.time() + ADME_TIMEOUT_SECONDS
        while status not in ("succeeded", "failed") and time.time() < deadline:
            time.sleep(POLL_SECONDS)
            pred = client.predictions.adme.retrieve(pred_id)
            status = getattr(pred, "status", None)

        d = _to_dict(pred)
        if _dig(d, "status") != "succeeded":
            result["error"] = _dig(d, "error", "message") or f"status={_dig(d, 'status')}"
            return result

        mols = _dig(d, "output", "molecules") or []
        if mols:
            m = mols[0]
            if m.get("status") == "succeeded":
                adme = m.get("adme") or {}
                result["lipophilicity"] = adme.get("lipophilicity")
                result["permeability"] = adme.get("permeability")
                result["solubility"] = adme.get("solubility")
                result["available"] = True
            else:
                result["error"] = _dig(m, "error", "message") or "molecule failed"
                return result
    except Exception as e:
        result["error"] = str(e)
        print(f"[boltz] predict_adme FAILED: {e}")
        return result

    cache_set(cache_key, result, ttl_days=30)
    return result
