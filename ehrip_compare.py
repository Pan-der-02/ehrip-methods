#!/usr/bin/env python3
"""
E-HRIP compared with common approaches on the same public Medicare data.

DESIGN (fixed and published before this script is run on real data)
  Data and eligibility are identical to ehrip_provider.py version 0.3.1: CMS "Medicare Physician & Other
  Practitioners - by Provider and Service" rows with at least 11 beneficiaries; peer groups are a service
  code (HCPCS) in a place of service, with at least 30 rows.

  Review budget K: the number of providers the E-HRIP rule flags. Every approach selects K providers.
    1. E-HRIP: the providers its rule flags (robust z-score of standardized Medicare payment per
       beneficiary above 6.0 within the peer group). Every flag carries a plain-language reason.
    2. Largest billers: the K providers with the highest total Medicare payment across all their rows.
    3. Classic outlier score: within each peer group, z = (x - mean) / standard deviation, where x is the
       actual (not standardized) Medicare payment per beneficiary. Each provider is scored by its highest
       z, and the K highest-scoring providers are selected.
    4. Random selection: the expected count, K x (share of all providers in the data on the LEIE).

  Outcome: the provider's NPI appears on the HHS-OIG List of Excluded Individuals/Entities (LEIE), and,
  separately, appears with an exclusion date after the data year.

  Paired comparison: for E-HRIP against each other approach, the LEIE providers selected by only one of
  the two are counted, and a one-sided exact binomial (sign) test asks whether E-HRIP's share of those
  discordant providers exceeds one half.

  Every approach is reported, whatever the result. Ties are broken by NPI, so results are reproducible.
  No provider-level list is written.

USAGE (with ehrip_provider.py in the same folder as this script)
  python ehrip_compare.py --input MUP_PHY_..._Prov_Svc.csv --leie UPDATED.csv --year 2023 --out run_compare
  python ehrip_compare.py --demo --out demo_compare     # toy data, code test only
"""
import argparse
import datetime
import json
import os
import platform
import sys

import numpy as np
import pandas as pd

import ehrip_provider as ep

__version__ = "0.4.0"
REQUIRED_PROVIDER_VERSION = "0.3.1"


def top_k(scores, k):
    """Top k NPIs by score (descending), ties broken by NPI (ascending); NaN scores never selected."""
    s = scores.dropna()
    s = s.reset_index()
    s.columns = ["npi", "score"]
    s = s.sort_values(["score", "npi"], ascending=[False, True])
    return set(s["npi"].head(k))


def classic_scores(d, cfg):
    x = d["srvcs"] * d["pay"] / d["benes"]          # actual Medicare payment per beneficiary
    g = x.groupby([d["hcpcs"], d["pos"]], observed=True)
    mean, sd, n = g.transform("mean"), g.transform("std"), g.transform("size")
    z = (x - mean) / sd
    z = z.where((n >= cfg["min_peer_rows"]) & (sd > 0))
    return z.groupby(d["npi"]).max()


def outcome(selected, base_npis, le, year):
    excl = set(le["npi"])
    sel = pd.Series(sorted(selected), dtype="int64")
    n_all = len(base_npis)
    k_all = int(base_npis.isin(excl).sum())
    base = k_all / n_all if n_all else 0.0
    n_sel = len(sel)
    hits = sel[sel.isin(excl)]
    k_sel = int(len(hits))
    out = {
        "providers_selected": n_sel, "on_leie": k_sel,
        "share_on_leie": round(k_sel / n_sel, 6) if n_sel else None,
        "lift_vs_all_providers": round((k_sel / n_sel) / base, 2) if n_sel and base > 0 else None,
        "p_value_one_sided_binomial": ep.binom_tail(k_sel, n_sel, base) if base > 0 else None,
    }
    if "excl_date" in le and year and str(year).isdigit():
        end = pd.Timestamp(f"{year}-12-31")
        dates = hits.map(le.set_index("npi")["excl_date"])
        out["on_leie_excluded_after_data_year"] = int((dates > end).sum())
    return out, set(hits)


def sign_test(b, c):
    """One-sided exact binomial test: P(X >= b) for X ~ Binomial(b + c, 0.5)."""
    n = b + c
    if n == 0:
        return None
    return ep.binom_tail(b, n, 0.5)


def write_note(path, man, demo):
    banner = "> **DEMO RUN ON TOY DATA. NOT EVIDENCE.**\n\n" if demo else ""
    A, P, base = man["approaches"], man["paired_comparison"], man["base"]

    def p_txt(p):
        return "n/a" if p is None else (f"{p:.2e}" if p < 0.001 else f"{p:.3f}")

    names = {"ehrip": "E-HRIP (robust peer comparison, standardized)", "largest_billers": "Largest billers (total Medicare payment)",
             "classic_outlier": "Classic outlier score (mean and standard deviation, actual payment)"}
    reasons = {"ehrip": "Yes", "largest_billers": "No", "classic_outlier": "No"}
    after = "on_leie_excluded_after_data_year" in A["ehrip"]
    head = "| Approach | Providers selected | On LEIE | Lift vs all providers |" + (" Excluded after data year |" if after else "") + " Reason per flag |"
    sep = "|---|---|---|---|" + ("---|" if after else "") + "---|"
    rows = []
    for k in ["ehrip", "largest_billers", "classic_outlier"]:
        a = A[k]
        rows.append(f"| {names[k]} | {a['providers_selected']:,} | {a['on_leie']} | {a['lift_vs_all_providers']}x |"
                    + (f" {a.get('on_leie_excluded_after_data_year')} |" if after else "") + f" {reasons[k]} |")
    rows.append(f"| Random selection (expected) | {man['budget_k']:,} | {base['expected_on_leie_if_random']} | 1.00x |"
                + (" n/a |" if after else "") + " No |")
    prow = "\n".join(
        f"| {names[k]} | {P[k]['leie_only_ehrip']} | {P[k]['leie_only_other']} | {P[k]['leie_both']} | {p_txt(P[k]['p_value_one_sided_sign_test'])} |"
        for k in ["largest_billers", "classic_outlier"])
    note = f"""{banner}# E-HRIP Comparison with Common Approaches: Run Note

**Run (UTC):** {man['run_utc']} | **Comparison version:** {__version__} | **E-HRIP version:** {man['ehrip_version']} | **Data year:** {man['data_year']}
**CMS file:** {man['source']} (SHA-256 {man['input_sha256']})
**LEIE file:** {man['leie_source']} (SHA-256 {man['leie_sha256']})

## Design (fixed before the run)
Every approach selects the same number of providers: the {man['budget_k']:,} flagged by the E-HRIP rule. Data and
eligibility are the same for all approaches ({base['providers_in_data']:,} providers). The outcome is whether a selected
provider's NPI appears on the HHS-OIG LEIE ({base['providers_on_leie']} of all providers in the data, {base['share_on_leie']:.4%}).

## Results
{head}
{sep}
{chr(10).join(rows)}

## Paired comparison (LEIE providers selected by only one approach)
| E-HRIP compared with | Found only by E-HRIP | Found only by the other | Found by both | One-sided exact sign test p |
|---|---|---|---|---|
{prow}

## How to read this
All approaches select the same number of providers from the same data, so the counts compare directly. The
LEIE is an external signal, not proof about any selected provider or payment. Only E-HRIP states a reason for
each selection; the other approaches give a ranking or a number.
"""
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(note)


def main():
    ap = argparse.ArgumentParser(description="E-HRIP compared with common approaches on public Medicare data")
    ap.add_argument("--input", help="CMS by Provider and Service CSV")
    ap.add_argument("--leie", help="HHS-OIG LEIE full database CSV")
    ap.add_argument("--year", help="data year of the CMS file, e.g. 2023")
    ap.add_argument("--out", default="run_compare")
    ap.add_argument("--demo", action="store_true")
    ap.add_argument("--seed", type=int, default=20260101)
    a = ap.parse_args()
    if ep.__version__ != REQUIRED_PROVIDER_VERSION:
        sys.exit(f"This comparison expects ehrip_provider.py version {REQUIRED_PROVIDER_VERSION}; found {ep.__version__}.")
    if not a.demo and not (a.input and a.leie):
        sys.exit("Provide --input and --leie, or --demo")
    os.makedirs(a.out, exist_ok=True)
    if a.demo:
        df, le = ep.make_demo(a.seed)
        src = lsrc = "TOY DEMO DATA"; digest = ldigest = None; year = "demo"
    else:
        df, _ = ep.load_cms(a.input)
        le, _ = ep.load_leie(a.leie)
        src, lsrc = os.path.basename(a.input), os.path.basename(a.leie)
        digest, ldigest, year = ep.sha256(a.input), ep.sha256(a.leie), a.year

    d, f, _ = ep.detect(df, ep.CFG)
    base_npis = pd.Series(d["npi"].unique()).astype("int64")
    ehrip_set = set(f["npi"].astype("int64").unique())
    k = len(ehrip_set)

    billers = d.groupby("npi")["paid"].sum()
    billers.index = billers.index.astype("int64")
    classic = classic_scores(d, ep.CFG)
    classic.index = classic.index.astype("int64")
    sets = {"ehrip": ehrip_set, "largest_billers": top_k(billers, k), "classic_outlier": top_k(classic, k)}

    approaches, leie_hits = {}, {}
    for name, s in sets.items():
        approaches[name], leie_hits[name] = outcome(s, base_npis, le, year)
    excl = set(le["npi"])
    k_all = int(base_npis.isin(excl).sum())
    base = {"providers_in_data": int(len(base_npis)), "providers_on_leie": k_all,
            "share_on_leie": k_all / len(base_npis) if len(base_npis) else 0.0,
            "expected_on_leie_if_random": round(k * k_all / len(base_npis), 2) if len(base_npis) else None}
    paired = {}
    for name in ["largest_billers", "classic_outlier"]:
        b = len(leie_hits["ehrip"] - leie_hits[name])
        c = len(leie_hits[name] - leie_hits["ehrip"])
        paired[name] = {"leie_only_ehrip": b, "leie_only_other": c, "leie_both": len(leie_hits["ehrip"] & leie_hits[name]),
                        "providers_overlap": len(sets["ehrip"] & sets[name]), "p_value_one_sided_sign_test": sign_test(b, c)}

    tag = "DEMO_" if a.demo else ""
    man = {
        "run_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        "version": __version__, "ehrip_version": ep.__version__, "data_year": year,
        "source": src, "input_sha256": digest, "leie_source": lsrc, "leie_sha256": ldigest,
        "config": ep.CFG, "budget_k": k, "base": base, "approaches": approaches, "paired_comparison": paired,
        "python": platform.python_version(), "pandas": pd.__version__, "numpy": np.__version__, "demo": bool(a.demo),
    }
    with open(os.path.join(a.out, f"{tag}comparison_manifest.json"), "w") as fh:
        json.dump(man, fh, indent=2, default=str)
    write_note(os.path.join(a.out, f"{tag}COMPARISON_NOTE.md"), man, a.demo)
    print(json.dumps({"budget_k": k, "base": base, "approaches": approaches, "paired_comparison": paired}, indent=2, default=str))


if __name__ == "__main__":
    main()
