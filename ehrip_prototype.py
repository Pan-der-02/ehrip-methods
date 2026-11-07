#!/usr/bin/env python3
"""
E-HRIP explainable claim-flagging prototype.

WHAT THIS IS
  A small, reproducible pipeline that:
    1. loads a claims file (built for CMS's public Synthetic Medicare Claims PUFs),
    2. rolls claim lines up to one row per claim,
    3. applies three transparent review rules,
    4. writes one plain-language reason for every flag, and
    5. runs a CONTROLLED TEST: known problems are planted in a copy of the data, the
       pipeline is scored on how many it finds again (recall), and that recall is
       compared with what random selection of the same number of claims would find.

WHAT THIS IS NOT
  - It does not decide that any claim is improper or fraudulent. Flags are for human review.
  - Synthetic data contains no known real payment errors. Results show the method works
    as designed on planted cases. They do not measure real-world accuracy, precision,
    false-positive rates, savings, or anything about real Medicare beneficiaries.
  - Results from --demo (toy data generated in this script) are not evidence of anything.

DATA
  CMS Synthetic Medicare Enrollment, Fee-for-Service Claims, and Prescription Drug Event
  Data (Synthetic Medicare Claims PUFs):
  https://data.cms.gov/collection/synthetic-medicare-enrollment-fee-for-service-claims-and-prescription-drug-event
  Files are pipe-delimited. Use carrier.csv or outpatient.csv.

USAGE
  python ehrip_prototype.py --input carrier.csv --out run_carrier
  python ehrip_prototype.py --input outpatient.csv --out run_outpatient
  python ehrip_prototype.py --input carrier.csv --map my_columns.json --out run_carrier
  python ehrip_prototype.py --demo --out demo_run          # code test only
"""
import argparse
import csv
import datetime
import hashlib
import json
import os
import platform
import sys

import numpy as np
import pandas as pd

__version__ = "0.2.0"

# For each field the pipeline needs, the column names it will look for, in order of preference.
# Names follow the CMS Chronic Conditions Warehouse conventions used by the synthetic files.
# If your file uses different names, pass --map with a JSON file such as {"pay_amt": "MY_COLUMN"}.
CANDIDATES = {
    "claim_id": ["CLM_ID"],
    "bene_id": ["BENE_ID", "DESYNPUF_ID"],
    "provider": ["PRF_PHYSN_NPI", "RNDRNG_PHYSN_NPI", "ORG_NPI_NUM", "PRVDR_NUM", "CARR_PRFRNG_PIN_NUM"],
    "from_dt": ["CLM_FROM_DT"],
    "thru_dt": ["CLM_THRU_DT"],
    "pay_amt": ["CLM_PMT_AMT", "LINE_NCH_PMT_AMT"],
    "peer_key": ["LINE_HCPCS_CD", "HCPCS_CD", "PRNCPAL_DGNS_CD", "ICD_DGNS_CD1", "ICD9_DGNS_CD_1"],
}
REQUIRED = ["claim_id", "bene_id", "provider", "from_dt", "thru_dt", "pay_amt", "peer_key"]

CFG = {
    "robust_z_threshold": 6.0,  # amount-outlier rule: robust z-score above this is flagged
    "min_peer_claims": 30,      # smallest peer group (same service or diagnosis code) used
    "inject_n_each": 200,       # controlled test: planted problems per type
    "inflate_factor": 8.0,      # controlled test: payment multiplier for inflated claims
}

RULES = ["possible_duplicate", "date_order", "amount_outlier"]


# ---------------------------------------------------------------- loading

def sniff_delimiter(path):
    with open(path, "r", newline="", encoding="utf-8", errors="replace") as fh:
        head = fh.readline()
    counts = {d: head.count(d) for d in ["|", ",", "\t"]}
    return max(counts, key=counts.get)


def resolve_columns(header, override):
    chosen, missing = {}, []
    for field in REQUIRED:
        if field in override:
            if override[field] not in header:
                sys.exit(f"--map says {field} is column '{override[field]}', but that column is not in the file.")
            chosen[field] = override[field]
            continue
        hit = next((c for c in CANDIDATES[field] if c in header), None)
        if hit:
            chosen[field] = hit
        else:
            missing.append(field)
    if missing:
        sys.exit(
            "Could not find columns for: " + ", ".join(missing) + "\n"
            "Columns in the file: " + ", ".join(header) + "\n"
            "Create a JSON file mapping each missing field to a column name and pass it with --map."
        )
    return chosen


def parse_dates(s):
    s = s.astype("string").str.strip()
    for fmt in ("%Y%m%d", "%Y-%m-%d", "%d-%b-%Y", "%m/%d/%Y"):
        out = pd.to_datetime(s, format=fmt, errors="coerce")
        if out.notna().mean() > 0.9:
            return out
    return pd.to_datetime(s, errors="coerce")


def load_claims(path, override):
    sep = sniff_delimiter(path)
    header = pd.read_csv(path, sep=sep, nrows=0).columns.tolist()
    cols = resolve_columns(header, override)
    raw = pd.read_csv(path, sep=sep, dtype=str, usecols=sorted(set(cols.values())), low_memory=False)
    lines = pd.DataFrame({k: raw[v] for k, v in cols.items()})
    lines["pay_amt"] = pd.to_numeric(lines["pay_amt"], errors="coerce")
    lines["from_dt"] = parse_dates(lines["from_dt"])
    lines["thru_dt"] = parse_dates(lines["thru_dt"])

    # Roll lines up to one row per claim. A claim-level amount repeats on every line, so take
    # the first value; a line-level amount (LINE_*) is summed across the claim's lines.
    line_level_pay = cols["pay_amt"].startswith("LINE_")
    agg = {
        "bene_id": "first", "provider": "first", "from_dt": "min", "thru_dt": "max",
        "pay_amt": "sum" if line_level_pay else "first", "peer_key": "first",
    }
    claims = lines.groupby("claim_id", as_index=False).agg(agg)
    info = {"delimiter": sep, "columns_used": cols, "claim_lines_read": int(len(lines)),
            "pay_amt_rollup": "sum of line amounts" if line_level_pay else "claim-level amount"}
    return claims, info


def make_demo_data(seed, n=20000):
    rng = np.random.default_rng(seed)
    code = rng.integers(0, 40, n)
    base = 100 + code * 15
    start = pd.Timestamp("2021-01-01")
    frm = start + pd.to_timedelta(rng.integers(0, 1000, n), unit="D")
    thru = frm + pd.to_timedelta(rng.integers(0, 4, n), unit="D")
    return pd.DataFrame({
        "claim_id": [f"DEMO{i:07d}" for i in range(n)],
        "bene_id": [f"B{b:05d}" for b in rng.integers(0, 5000, n)],
        "provider": [f"P{p:04d}" for p in rng.integers(0, 300, n)],
        "from_dt": frm, "thru_dt": thru,
        "pay_amt": np.round(rng.lognormal(np.log(base), 0.5), 2),
        "peer_key": [f"CODE{c:02d}" for c in code],
    })


# ---------------------------------------------------------------- rules

def detect(df, cfg):
    """Return (flags, notes). flags has one row per flag: claim_id, rule, severity, reason."""
    flags, notes = [], {}

    # Rule 1: possible duplicate (same beneficiary, provider, service dates, payment amount)
    key = ["bene_id", "provider", "from_dt", "thru_dt", "pay_amt"]
    d = df[df.duplicated(key, keep=False) & df["from_dt"].notna()].copy()
    if len(d):
        d["first_id"] = d.groupby(key, dropna=False)["claim_id"].transform("first")
        d["n_in_group"] = d.groupby(key, dropna=False)["claim_id"].transform("size")
        d = d[d["claim_id"] != d["first_id"]]
        for r in d.itertuples():
            flags.append((r.claim_id, "possible_duplicate", 3,
                          f"Same beneficiary, provider, service dates and payment amount (${r.pay_amt:,.2f}) "
                          f"as claim {r.first_id}; {r.n_in_group} matching claims in total."))

    # Rule 2: service end date earlier than start date
    bad = df[df["thru_dt"] < df["from_dt"]]
    for r in bad.itertuples():
        flags.append((r.claim_id, "date_order", 2,
                      f"Service end date ({r.thru_dt.date()}) is earlier than start date ({r.from_dt.date()})."))

    # Rule 3: payment far above peers with the same service or diagnosis code (robust z on median/MAD)
    g = df.dropna(subset=["pay_amt", "peer_key"]).groupby("peer_key")["pay_amt"]
    med, cnt = g.median(), g.count()
    mad = g.apply(lambda s: (s - s.median()).abs().median())
    ok = cnt[cnt >= cfg["min_peer_claims"]].index
    notes["peer_groups_used"] = int(len(ok))
    notes["peer_groups_skipped_small"] = int((cnt < cfg["min_peer_claims"]).sum())
    notes["peer_groups_skipped_zero_mad"] = int(((mad == 0) & cnt.index.isin(ok)).sum())
    sub = df[df["peer_key"].isin(ok)].dropna(subset=["pay_amt"]).copy()
    sub["med"] = sub["peer_key"].map(med)
    sub["mad"] = sub["peer_key"].map(mad)
    sub = sub[sub["mad"] > 0]
    sub["rz"] = 0.6745 * (sub["pay_amt"] - sub["med"]) / sub["mad"]
    for r in sub[sub["rz"] > cfg["robust_z_threshold"]].itertuples():
        flags.append((r.claim_id, "amount_outlier", 1,
                      f"Payment ${r.pay_amt:,.2f} is {r.pay_amt / r.med:.1f}x the median (${r.med:,.2f}) "
                      f"for comparable claims with code {r.peer_key} (robust z = {r.rz:.1f})."))

    return pd.DataFrame(flags, columns=["claim_id", "rule", "severity", "reason"]), notes


# ---------------------------------------------------------------- controlled test

def controlled_test(df, cfg, seed):
    """Plant known problems in a copy of df, run detect(), and report recall by type,
    alongside the recall that random selection of the same number of claims would achieve."""
    rng = np.random.default_rng(seed + 1)
    n = cfg["inject_n_each"]
    base = df.dropna(subset=["pay_amt", "from_dt", "thru_dt", "peer_key"])
    if len(base) < 3 * n:
        sys.exit(f"Need at least {3 * n} complete claims for the controlled test; found {len(base)}.")
    pick = rng.choice(base.index.values, size=3 * n, replace=False)
    dup_i, inf_i, dat_i = pick[:n], pick[n:2 * n], pick[2 * n:]

    work = df.copy()
    planted = {}
    dups = work.loc[dup_i].copy()
    dups["claim_id"] = ["INJDUP" + str(i) for i in range(len(dups))]
    planted.update({c: "possible_duplicate" for c in dups["claim_id"]})
    work = pd.concat([work, dups], ignore_index=True)

    work.loc[inf_i, "pay_amt"] = work.loc[inf_i, "pay_amt"] * cfg["inflate_factor"]
    planted.update({c: "amount_outlier" for c in work.loc[inf_i, "claim_id"]})

    f, t = work.loc[dat_i, "from_dt"].copy(), work.loc[dat_i, "thru_dt"].copy()
    work.loc[dat_i, "from_dt"] = t.values + pd.Timedelta(days=1)  # end date now precedes start date
    work.loc[dat_i, "thru_dt"] = f.values
    planted.update({c: "date_order" for c in work.loc[dat_i, "claim_id"]})

    flags, _ = detect(work, cfg)
    found = flags.groupby("claim_id")["rule"].apply(set)
    total = len(work)
    res = {"claims_in_test_copy": int(total)}
    for rule in RULES:
        ids = [c for c, r in planted.items() if r == rule]
        hit = sum(1 for c in ids if c in found.index and rule in found[c])
        k = int((flags["rule"] == rule).sum())  # claims this rule flagged in the test copy
        res[rule] = {
            "planted": len(ids),
            "found": hit,
            "recall": round(hit / len(ids), 3) if ids else None,
            "claims_flagged_by_rule": k,
            "random_selection_expected_recall": round(k / total, 4) if total else None,
        }
    unplanted = work.loc[~work["claim_id"].isin(planted.keys()), "claim_id"]
    res["flags_on_unplanted_claims"] = int(flags["claim_id"].isin(unplanted).sum())
    res["unplanted_claims"] = int(len(unplanted))
    res["note"] = ("Synthetic data has no known real errors, so flags on unplanted claims cannot be scored as right "
                   "or wrong; precision and false-positive rates are not measured. Random-selection recall is the "
                   "share of planted cases expected to be caught by flagging the same number of claims at random.")
    return res


# ---------------------------------------------------------------- outputs

def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def write_note(path, manifest, demo):
    t = manifest["controlled_test"]
    banner = "> **DEMO RUN ON TOY DATA. NOT EVIDENCE.**\n\n" if demo else ""
    rows = {
        "possible_duplicate": "Duplicate claim",
        "amount_outlier": "Inflated payment",
        "date_order": "Date-order error",
    }
    table = "\n".join(
        f"| {label} | {t[r]['planted']} | {t[r]['found']} | {t[r]['recall']} | {t[r]['random_selection_expected_recall']} |"
        for r, label in rows.items()
    )
    cols = manifest.get("load_info", {}).get("columns_used", {})
    note = f"""{banner}# E-HRIP Prototype: Run Note

**Run (UTC):** {manifest['run_utc']} | **Source:** {manifest['source']} | **Input SHA-256:** {manifest['input_sha256']}
**Claims analyzed:** {manifest['claims_loaded']:,} | **Seed:** {manifest['seed']} | **Version:** {__version__} | **Python/pandas:** {manifest['python']}/{manifest['pandas']}

## Columns used
{', '.join(f'{k} = {v}' for k, v in cols.items()) or 'Demo data'}

## Rules
Every flag carries a plain-language reason. A reviewer makes every decision.
1. **possible_duplicate**: same beneficiary, provider, service dates, and payment amount as another claim.
2. **date_order**: service end date earlier than start date.
3. **amount_outlier**: payment above comparable claims with the same service or diagnosis code (robust z-score on median and median absolute deviation above {manifest['config']['robust_z_threshold']}; groups of at least {manifest['config']['min_peer_claims']} claims).

## Controlled test
Known problems were planted in a copy of the data, and the pipeline was scored on finding them again.

| Planted problem | Planted | Found | Recall | Random selection (expected recall) |
|---|---|---|---|---|
{table}

The planted duplicates and date-order errors match the rule definitions exactly, so their recall confirms the
rules work as written. The inflated-payment test is the more informative one, because the pipeline has to find
planted payments among naturally varying amounts.

Flags on unplanted claims: {t['flags_on_unplanted_claims']:,} of {t['unplanted_claims']:,}. {t['note']}

## Limitations
The data is synthetic and contains no known real payment errors. These results show the pipeline runs
reproducibly and finds problems that were deliberately planted. They do not establish real-world accuracy,
precision, false-positive rates, savings, or any conclusion about Medicare beneficiaries. Real-world
evaluation would happen only with an organization's data and approval.
"""
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(note)


def main():
    ap = argparse.ArgumentParser(description="E-HRIP explainable claim-flagging prototype")
    ap.add_argument("--input", help="claims file (e.g., carrier.csv from the CMS synthetic PUFs)")
    ap.add_argument("--map", help="optional JSON file mapping fields to column names")
    ap.add_argument("--demo", action="store_true", help="run on generated toy data (code test only)")
    ap.add_argument("--out", default="run", help="output folder")
    ap.add_argument("--seed", type=int, default=20260101)
    a = ap.parse_args()
    if not a.input and not a.demo:
        sys.exit("Provide --input FILE or --demo")

    os.makedirs(a.out, exist_ok=True)
    if a.demo:
        df, info = make_demo_data(a.seed), {"columns_used": {}}
        src, digest = "TOY DEMO DATA (generated in-script)", None
    else:
        override = json.load(open(a.map)) if a.map else {}
        df, info = load_claims(a.input, override)
        src, digest = os.path.basename(a.input), sha256(a.input)

    flags, notes = detect(df, CFG)
    test = controlled_test(df, CFG, a.seed)
    tag = "DEMO_" if a.demo else ""
    flags.to_csv(os.path.join(a.out, f"{tag}flags.csv"), index=False)

    manifest = {
        "run_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        "version": __version__, "source": src, "input_sha256": digest, "seed": a.seed,
        "config": CFG, "load_info": info, "claims_loaded": int(len(df)),
        "claims_flagged": int(flags["claim_id"].nunique()),
        "flags_by_rule": flags["rule"].value_counts().to_dict(),
        "peer_group_notes": notes, "controlled_test": test,
        "python": platform.python_version(), "pandas": pd.__version__, "numpy": np.__version__,
        "demo": bool(a.demo),
    }
    with open(os.path.join(a.out, f"{tag}run_manifest.json"), "w") as fh:
        json.dump(manifest, fh, indent=2, default=str)
    write_note(os.path.join(a.out, f"{tag}RUN_NOTE.md"), manifest, a.demo)
    print(json.dumps({k: manifest[k] for k in ["claims_loaded", "claims_flagged", "flags_by_rule", "controlled_test"]},
                     indent=2))


if __name__ == "__main__":
    main()
