"""
Extract the small set of DrugCentral tables the harness needs from the
1.4 GB pg_dump gzip, and cache the result as JSON so later runs are instant.

Only two facts are pulled from DrugCentral, and only as a *sanity filter*
(is this DrugBank drug a real, established product?):
  - DRUGBANK_ID  -> struct_id            (from `identifier`)
  - struct_id    -> earliest approval    (min date over `approval` rows)

No per-indication sequencing is done here (DrugCentral has no per-indication
approval dates; verified against the real dump).
"""
from __future__ import annotations

import gzip
import json
import os

RAW_DIR = os.path.join(os.path.dirname(__file__), "raw")
DUMP_PATH = os.path.join(RAW_DIR, "dc_dump.sql.gz")
CACHE_PATH = os.path.join(os.path.dirname(__file__), "output", "drugcentral_extract.json")

_WANT = {"public.approval", "public.identifier"}


def _parse_dump() -> dict:
    blocks: dict[str, dict] = {}
    cur = None
    with gzip.open(DUMP_PATH, "rt", errors="replace") as f:
        for line in f:
            if line.startswith("COPY "):
                tbl = line.split()[1]
                if tbl in _WANT:
                    cols = line[line.find("(") + 1 : line.find(")")].split(", ")
                    blocks[tbl] = {"cols": cols, "rows": []}
                    cur = tbl
                continue
            if cur is not None:
                if line.startswith("\\."):
                    cur = None
                    continue
                blocks[cur]["rows"].append(line.rstrip("\n").split("\t"))

    ident = blocks["public.identifier"]
    ic = ident["cols"]
    I_id, I_ty, I_st = ic.index("identifier"), ic.index("id_type"), ic.index("struct_id")
    drugbank_to_struct = {
        r[I_id]: r[I_st] for r in ident["rows"] if r[I_ty] == "DRUGBANK_ID"
    }

    appr = blocks["public.approval"]
    ac = appr["cols"]
    A_st, A_dt = ac.index("struct_id"), ac.index("approval")
    struct_dates: dict[str, list[str]] = {}
    for r in appr["rows"]:
        d = r[A_dt]
        if d not in ("", "\\N"):
            struct_dates.setdefault(r[A_st], []).append(d)
    struct_min_date = {s: min(v) for s, v in struct_dates.items()}

    return {
        "drugbank_to_struct": drugbank_to_struct,
        "struct_min_date": struct_min_date,
    }


def load(force: bool = False) -> dict:
    """Return {drugbank_to_struct, struct_min_date}, using the JSON cache."""
    os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)
    if not force and os.path.exists(CACHE_PATH):
        with open(CACHE_PATH) as f:
            return json.load(f)
    data = _parse_dump()
    with open(CACHE_PATH, "w") as f:
        json.dump(data, f)
    return data


if __name__ == "__main__":
    d = load(force=True)
    print("drugbank_to_struct:", len(d["drugbank_to_struct"]))
    print("struct_min_date:", len(d["struct_min_date"]))
