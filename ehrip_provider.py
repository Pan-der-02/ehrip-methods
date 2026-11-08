#!/usr/bin/env python3
"""
E-HRIP provider-level review flags on real, public Medicare data.

WHAT THIS IS
  Applies the E-HRIP robust peer comparison to CMS's public "Medicare Physician & Other
  Practitioners - by Provider and Service" data, which reports real Medicare payments
  summarized by provider (NPI), service code (HCPCS), and place of service.

  For each row (one provider x one service code x one place of service) it computes the
  standardized Medicare payment per beneficiary, compares it with every other provider
  billing the same code in the same place of service, and flags rows far above their peers.
  Every flag carries a plain-language reason. It then reports:
    - how many rows and providers were flagged, what Medicare paid on those rows, and how far
      those payments exceed what the peer median would have been for the same beneficiaries;
    - whether flagged providers appear on the HHS-OIG List of Excluded Individuals/Entities
      (LEIE) more often than other providers in the same data.

WHAT THIS IS NOT
  - A flag is a reason to review, not a finding that any payment was improper.
  - The data are summaries, not claims. Legitimate differences in patient mix or specialty
    can produce high values.
  - Appearing on the LEIE is an external signal, not proof that a flagged payment was improper.

DATA
  CMS: https://data.cms.gov  ->  Medicare Physician & Other Practitioners - by Provider and Service
       (the full national file, or a filtered export, as CSV)
  HHS-OIG LEIE: https://oig.hhs.gov/exclusions/exclusions_list.asp  ->  full database CSV

VERSION NOTE
  0.3.1: a peer group whose median absolute deviation is zero up to floating-point rounding
         (below one billionth of the group median) is now treated as zero spread and skipped,
         as the rule intends. Version 0.3.0 compared MAD with exactly 0, so 24 code groups whose
         spread was zero apart from rounding error (about 1e-14) were used, and small differences
         in them produced extreme z-scores. No other change.

USAGE
  python ehrip_provider.py --input MUP_PHY_..._Prov_Svc.csv --leie UPDATED.csv --year 2023 --out run_provider
  python ehrip_provider.py --demo --out demo_provider     # toy data, code test only
"""
import argparse
import datetime
import hashlib
import json
import math
import os
import platform
import sys

import numpy as np
import pandas as pd

__version__ = "0.3.1"

# Settings are fixed before any real data is analyzed.
CFG = {
    "robust_z_threshold": 6.0,   # flag rows whose robust z-score is above this
    "min_peer_rows": 30,         # smallest peer group (same HCPCS code and place of service) used
    "min_benes": 11,             # rows below this beneficiary count are skipped (CMS suppresses <11)
    "measure": "standardized Medicare payment per beneficiary",
    "zero_spread_tolerance": 1e-9,  # a peer group's MAD below this fraction of its median counts as zero
}

CMS_COLS = {
    "npi": ["Rndrng_NPI"],
    "prov_type": ["Rndrng_Prvdr_Type"],
    "state": ["Rndrng_Prvdr_State_Abrvtn"],
    "hcpcs": ["HCPCS_Cd"],
    "hcpcs_desc": ["HCPCS_Desc"],
    "pos": ["Place_Of_Srvc"],
    "benes": ["Tot_Benes"],
    "srvcs": ["Tot_Srvcs"],
    "pay": ["Avg_Mdcr_Pymt_Amt"],
    "stdzd": ["Avg_Mdcr_Stdzd_Amt"],
}
REQUIRED = ["npi", "hcpcs", "pos", "benes", "srvcs", "pay", "stdzd"]


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def pick(header, names):
    low = {c.lower(): c for c in header}
    for n in names:
        if n in header:
            return n
        if n.lower() in low:
            return low[n.lower()]
    return None


def load_cms(path):
    header = pd.read_csv(path, nrows=0).columns.tolist()
    cols = {k: pick(header, v) for k, v in CMS_COLS.items()}
    missing = [k for k in REQUIRED if cols[k] is None]
    if missing:
        sys.exit("Missing CMS columns for: " + ", ".join(missing) + "\nColumns in file: " + ", ".join(header))
    use = {v: k for k, v in cols.items() if v is not None}
    # Memory-efficient types so the full national file (about 9.6 million rows) fits on a laptop.
    dtypes = {}
    for src, key in use.items():
        if key in ("benes", "srvcs", "pay", "stdzd"):
            dtypes[src] = "float64"
        elif key == "npi":
            dtypes[src] = "Int64"
        else:
            dtypes[src] = "category"
    df = pd.read_csv(path, usecols=list(use), dtype=dtypes, low_memory=False).rename(columns=use)
    df = df[df["npi"].notna()]
    for c in ["prov_type", "state", "hcpcs_desc"]:
        if c not in df:
            df[c] = ""
    return df, {"columns_used": cols, "rows_read": int(len(df))}


def load_leie(path):
    header = pd.read_csv(path, nrows=0).columns.tolist()
    npi_c = pick(header, ["NPI"])
    date_c = pick(header, ["EXCLDATE", "EXCL_DATE"])
    type_c = pick(header, ["EXCLTYPE", "EXCL_TYPE"])
    if npi_c is None:
        sys.exit("No NPI column in the LEIE file. Columns: " + ", ".join(header))
    use = [c for c in [npi_c, date_c, type_c] if c]
    le = pd.read_csv(path, usecols=use, dtype=str).rename(columns={npi_c: "npi", date_c or "": "excl_date", type_c or "": "excl_type"})
    le["npi"] = le["npi"].str.strip()
    le = le[le["npi"].str.fullmatch(r"\d{10}", na=False) & (le["npi"] != "0000000000")]
    le["npi"] = le["npi"].astype("int64")
    if "excl_date" in le:
        le["excl_date"] = pd.to_datetime(le["excl_date"], format="%Y%m%d", errors="coerce")
    le = le.sort_values("excl_date").drop_duplicates("npi", keep="first") if "excl_date" in le else le.drop_duplicates("npi")
    return le, {"leie_rows_with_npi": int(len(le))}


def make_demo(seed, n_prov=4000):
    rng = np.random.default_rng(seed)
    rows = []
    codes = [f"9{9000 + i}" for i in range(25)]
    base = {c: 60 + 8 * i for i, c in enumerate(codes)}
    npis = [1000000000 + i for i in range(n_prov)]
    for npi in npis:
        for c in rng.choice(codes, size=rng.integers(3, 9), replace=False):
            benes = int(rng.integers(11, 400))
            spb = max(1.0, rng.lognormal(np.log(1.6), 0.35))
            price = base[c] * rng.lognormal(0, 0.05)
            rows.append((npi, "Internal Medicine", "IN", c, "", "O", benes, round(benes * spb), round(price, 2), round(price, 2)))
    df = pd.DataFrame(rows, columns=["npi", "prov_type", "state", "hcpcs", "hcpcs_desc", "pos", "benes", "srvcs", "pay", "stdzd"])
    bad = rng.choice(npis, size=40, replace=False)
    m = df["npi"].isin(bad)
    df.loc[m, "srvcs"] = (df.loc[m, "srvcs"] * 9).round()
    le = pd.DataFrame({"npi": list(bad[:15]) + list(rng.choice(npis, size=15, replace=False)),
                       "excl_date": pd.to_datetime("2024-06-01"), "excl_type": "1128a1"})
    return df, le


def detect(df, cfg):
    d = df[(df["benes"] >= cfg["min_benes"]) & df["srvcs"].notna() & df["stdzd"].notna() & df["pay"].notna()].copy()
    d["ppb"] = d["srvcs"] * d["stdzd"] / d["benes"]             # standardized payment per beneficiary
    d["paid"] = d["srvcs"] * d["pay"]                             # actual Medicare payment on the row
    keys = ["hcpcs", "pos"]
    g = d.groupby(keys, observed=True)["ppb"]
    d["n_peers"] = g.transform("size")
    d["med"] = g.transform("median")
    d["absdev"] = (d["ppb"] - d["med"]).abs()
    d["mad"] = d.groupby(keys, observed=True)["absdev"].transform("median")
    # spread that is zero apart from floating-point rounding counts as zero (see VERSION NOTE)
    tol = cfg["zero_spread_tolerance"] * d["med"].abs().clip(lower=1.0)
    d.loc[d["mad"] <= tol, "mad"] = 0.0
    groups = d.groupby(keys, observed=True).agg(n=("ppb", "size"), mad=("mad", "first"))
    notes = {
        "peer_groups_total": int(len(groups)),
        "peer_groups_used": int(((groups["n"] >= cfg["min_peer_rows"]) & (groups["mad"] > 0)).sum()),
        "peer_groups_skipped_small": int((groups["n"] < cfg["min_peer_rows"]).sum()),
        "peer_groups_skipped_zero_mad": int(((groups["n"] >= cfg["min_peer_rows"]) & (groups["mad"] == 0)).sum()),
        "rows_analyzed": int(len(d)),
        "rows_in_used_groups": int(((d["n_peers"] >= cfg["min_peer_rows"]) & (d["mad"] > 0)).sum()),
        "providers_analyzed": int(d["npi"].nunique()),
        "medicare_paid_analyzed": float(d["paid"].sum()),
    }
    ok = (d["n_peers"] >= cfg["min_peer_rows"]) & (d["mad"] > 0)
    d["rz"] = np.where(ok, 0.6745 * (d["ppb"] - d["med"]) / d["mad"].where(d["mad"] > 0), np.nan)
    f = d[d["rz"] > cfg["robust_z_threshold"]].copy()
    # dollars above what the peer median would have been for the same beneficiaries, converted
    # from standardized to actual dollars using the row's own actual/standardized ratio
    ratio = (f["pay"] / f["stdzd"]).replace([np.inf, -np.inf], np.nan).fillna(1.0)
    f["above_median_paid"] = ((f["ppb"] - f["med"]) * f["benes"] * ratio).clip(lower=0)
    place = {"F": "facility", "O": "non-facility"}
    f["reason"] = [
        f"Standardized Medicare payment of ${r.ppb:,.2f} per beneficiary for HCPCS {r.hcpcs} ({place.get(r.pos, r.pos)}) "
        f"is {r.ppb / r.med:.1f}x the median (${r.med:,.2f}) among {int(r.n_peers):,} providers billing this code "
        f"(robust z = {r.rz:.1f}); {int(r.benes):,} beneficiaries, {int(r.srvcs):,} services."
        for r in f.itertuples()
    ]
    return d, f, notes


def binom_tail(k, n, p):
    """P(X >= k) for X ~ Binomial(n, p), computed in log space."""
    if n == 0 or p <= 0:
        return None
    if k <= 0:
        return 1.0
    total = 0.0
    lp, lq = math.log(p), math.log1p(-p)
    terms = [math.lgamma(n + 1) - math.lgamma(i + 1) - math.lgamma(n - i + 1) + i * lp + (n - i) * lq for i in range(k, n + 1)]
    mx = max(terms)
    total = sum(math.exp(t - mx) for t in terms)
    return float(min(1.0, math.exp(mx) * total))


def leie_check(d, f, le, year):
    excl = set(le["npi"])
    all_npi = pd.Series(d["npi"].unique())
    flag_npi = pd.Series(f["npi"].unique())
    n_all, n_flag = len(all_npi), len(flag_npi)
    k_all = int(all_npi.isin(excl).sum())
    k_flag = int(flag_npi.isin(excl).sum())
    base = k_all / n_all if n_all else 0.0
    rate = k_flag / n_flag if n_flag else 0.0
    out = {
        "providers_in_data": n_all, "providers_in_data_on_leie": k_all, "share_in_data_on_leie": round(base, 6),
        "providers_flagged": n_flag, "providers_flagged_on_leie": k_flag, "share_flagged_on_leie": round(rate, 6),
        "lift": round(rate / base, 2) if base > 0 else None,
        "expected_flagged_on_leie_if_random": round(n_flag * base, 2),
        "p_value_one_sided_binomial": binom_tail(k_flag, n_flag, base) if base > 0 else None,
    }
    if "excl_date" in le and year:
        start, end = pd.Timestamp(f"{year}-01-01"), pd.Timestamp(f"{year}-12-31")
        m = le.set_index("npi")["excl_date"]
        hits = flag_npi[flag_npi.isin(excl)].map(m)
        out["flagged_on_leie_excluded_before_data_year"] = int((hits < start).sum())
        out["flagged_on_leie_excluded_during_data_year"] = int(((hits >= start) & (hits <= end)).sum())
        out["flagged_on_leie_excluded_after_data_year"] = int((hits > end).sum())
    return out


def write_note(path, man, demo):
    t, n, L = man["totals"], man["peer_group_notes"], man["leie_check"]
    banner = "> **DEMO RUN ON TOY DATA. NOT EVIDENCE.**\n\n" if demo else ""
    lift = "n/a" if L.get("lift") is None else f"{L['lift']}x"
    p = L.get("p_value_one_sided_binomial")
    ptxt = "n/a" if p is None else (f"{p:.2e}" if p < 0.001 else f"{p:.3f}")
    note = f"""{banner}# E-HRIP Provider-Level Run Note

**Run (UTC):** {man['run_utc']} | **Version:** {__version__} | **Data year:** {man['data_year']}
**CMS file:** {man['source']} (SHA-256 {man['input_sha256']})
**LEIE file:** {man['leie_source']} (SHA-256 {man['leie_sha256']})

## Rule (fixed before analysis)
For each provider, service code (HCPCS), and place of service, the standardized Medicare payment per
beneficiary is compared with all providers billing the same code in the same place of service. A row is
flagged when its robust z-score (median and median absolute deviation) is above {man['config']['robust_z_threshold']},
in peer groups of at least {man['config']['min_peer_rows']} providers. Standardized amounts remove geographic payment differences.
Peer groups with zero spread (MAD of zero, including zero apart from floating-point rounding) are skipped.

## Version note
Version 0.3.1 corrects one implementation detail of the rule above. Version 0.3.0 skipped a peer group only
when its MAD was exactly 0, so groups whose spread was zero apart from rounding error were used, and tiny
differences in them produced extreme z-scores. Version 0.3.1 treats a MAD below one billionth of the group
median as zero. No other change was made.

## Results
- Rows analyzed: {n['rows_analyzed']:,} ({n['providers_analyzed']:,} providers); Medicare paid on those rows: ${n['medicare_paid_analyzed']:,.0f}
- Peer groups used: {n['peer_groups_used']:,} (skipped: {n['peer_groups_skipped_small']:,} small, {n['peer_groups_skipped_zero_mad']:,} zero MAD)
- Rows flagged: {t['rows_flagged']:,}; providers flagged: {t['providers_flagged']:,}
- Medicare paid on flagged rows: ${t['medicare_paid_on_flagged_rows']:,.0f}
- Amount above the peer median for the same beneficiaries: ${t['amount_above_peer_median']:,.0f}

## Exclusion-list check (HHS-OIG LEIE, matched by NPI)
| | Providers | On LEIE | Share |
|---|---|---|---|
| All providers in data | {L['providers_in_data']:,} | {L['providers_in_data_on_leie']:,} | {L['share_in_data_on_leie']:.4%} |
| Flagged providers | {L['providers_flagged']:,} | {L['providers_flagged_on_leie']:,} | {L['share_flagged_on_leie']:.4%} |

Lift: {lift}. Expected on LEIE if flags were random: {L['expected_flagged_on_leie_if_random']}. One-sided binomial p-value: {ptxt}.
{"Flagged providers on LEIE by exclusion date relative to the data year: before " + str(L.get('flagged_on_leie_excluded_before_data_year')) + ", during " + str(L.get('flagged_on_leie_excluded_during_data_year')) + ", after " + str(L.get('flagged_on_leie_excluded_after_data_year')) + "." if 'flagged_on_leie_excluded_after_data_year' in L else ""}

## How to read this
A flag is a reason to review, not a finding that a payment was improper. The data are provider-level
summaries, and legitimate differences in patient mix or specialty can produce high values. The LEIE lists
exclusions currently in effect; appearing on it is an external signal, not proof about any flagged payment.
"""
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(note)


def main():
    ap = argparse.ArgumentParser(description="E-HRIP provider-level flags on public Medicare data")
    ap.add_argument("--input", help="CMS by Provider and Service CSV")
    ap.add_argument("--leie", help="HHS-OIG LEIE full database CSV")
    ap.add_argument("--year", help="data year of the CMS file, e.g. 2023")
    ap.add_argument("--out", default="run_provider")
    ap.add_argument("--demo", action="store_true")
    ap.add_argument("--seed", type=int, default=20260101)
    a = ap.parse_args()
    if not a.demo and not (a.input and a.leie):
        sys.exit("Provide --input and --leie, or --demo")
    os.makedirs(a.out, exist_ok=True)
    if a.demo:
        df, le = make_demo(a.seed)
        info, linfo = {"columns_used": {}}, {}
        src = lsrc = "TOY DEMO DATA"; digest = ldigest = None; year = "demo"
    else:
        df, info = load_cms(a.input)
        le, linfo = load_leie(a.leie)
        src, lsrc = os.path.basename(a.input), os.path.basename(a.leie)
        digest, ldigest, year = sha256(a.input), sha256(a.leie), a.year
    d, f, notes = detect(df, CFG)
    L = leie_check(d, f, le, None if a.demo else year)
    tag = "DEMO_" if a.demo else ""
    excl = set(le["npi"])
    f["on_leie"] = f["npi"].isin(excl)
    cols = ["npi", "prov_type", "state", "hcpcs", "hcpcs_desc", "pos", "benes", "srvcs", "ppb", "med", "n_peers", "rz",
            "paid", "above_median_paid", "on_leie", "reason"]
    f.sort_values("rz", ascending=False)[cols].to_csv(os.path.join(a.out, f"{tag}provider_flags.csv"), index=False)
    man = {
        "run_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        "version": __version__, "data_year": year, "source": src, "input_sha256": digest,
        "leie_source": lsrc, "leie_sha256": ldigest, "config": CFG, "load_info": info, "leie_info": linfo,
        "peer_group_notes": notes,
        "totals": {
            "rows_flagged": int(len(f)), "providers_flagged": int(f["npi"].nunique()),
            "medicare_paid_on_flagged_rows": float(f["paid"].sum()),
            "amount_above_peer_median": float(f["above_median_paid"].sum()),
        },
        "leie_check": L,
        "python": platform.python_version(), "pandas": pd.__version__, "numpy": np.__version__, "demo": bool(a.demo),
    }
    with open(os.path.join(a.out, f"{tag}provider_manifest.json"), "w") as fh:
        json.dump(man, fh, indent=2, default=str)
    write_note(os.path.join(a.out, f"{tag}PROVIDER_RUN_NOTE.md"), man, a.demo)
    print(json.dumps({"totals": man["totals"], "leie_check": L, "peer_group_notes": notes}, indent=2, default=str))


if __name__ == "__main__":
    main()
