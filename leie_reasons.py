"""
leie_reasons.py  -  exclusion reasons for flagged providers on the HHS-OIG LEIE

Reads the provider-level flags table written by ehrip_provider.py and the
HHS-OIG LEIE file used in that run, and reports, for flagged providers that
appear on the LEIE, how many were excluded under each statutory authority
(42 U.S.C. 1320a-7) and when, relative to the data year.

Output contains counts only. No NPIs, names, or other identifiers are written.

Usage:
    python leie_reasons.py --flags provider_flags.csv --leie UPDATED.csv --year 2023 --out run_leie_reasons
"""
import argparse
import hashlib
import json
import os
from datetime import datetime, timezone

import pandas as pd

HEADINGS = {
    "1128a1": "Conviction of program-related crimes (a)(1)",
    "1128a2": "Conviction relating to patient abuse (a)(2)",
    "1128a3": "Felony conviction relating to health care fraud (a)(3)",
    "1128a4": "Felony conviction relating to controlled substance (a)(4)",
    "1128b1": "Conviction relating to fraud (b)(1)",
    "1128b3": "Misdemeanor conviction relating to controlled substance (b)(3)",
    "1128b4": "License revocation or suspension (b)(4)",
    "1128b7": "Fraud, kickbacks, and other prohibited activities (b)(7)",
}
GROUPS = {
    "License revocation or suspension": ["1128b4"],
    "Controlled-substance conviction": ["1128a4", "1128b3"],
    "Conviction relating to patient abuse or neglect": ["1128a2"],
    "Fraud and program-related convictions": ["1128a1", "1128a3", "1128b1", "1128b7"],
}


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--flags", required=True)
    ap.add_argument("--leie", required=True)
    ap.add_argument("--year", type=int, required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()

    le = pd.read_csv(a.leie, dtype=str, usecols=["NPI", "EXCLTYPE", "EXCLDATE"])
    le["NPI"] = le["NPI"].fillna("").str.strip()
    le = le[(le["NPI"] != "") & (le["NPI"] != "0")].copy()
    le["EXCLTYPE"] = le["EXCLTYPE"].str.strip()
    le["excl_date"] = pd.to_datetime(le["EXCLDATE"], format="%Y%m%d", errors="coerce")
    # Same rule as ehrip_provider.py: one row per NPI, earliest exclusion first.
    le = le.sort_values("excl_date").drop_duplicates("NPI", keep="first")

    f = pd.read_csv(a.flags, dtype={"npi": str}, usecols=["npi", "on_leie"])
    flagged_on_leie = set(f.loc[f["on_leie"] == True, "npi"].str.strip())  # noqa: E712
    m = le[le["NPI"].isin(flagged_on_leie)].copy()

    start = pd.Timestamp(f"{a.year}-01-01")
    end = pd.Timestamp(f"{a.year}-12-31")
    after = m["excl_date"] > end

    by_type = m["EXCLTYPE"].value_counts().to_dict()
    by_type_after = m.loc[after, "EXCLTYPE"].value_counts().to_dict()
    groups = {}
    for g, codes in GROUPS.items():
        sel = m["EXCLTYPE"].isin(codes)
        groups[g] = {"providers": int(sel.sum()), "excluded_after_data_year": int((sel & after).sum())}

    out = {
        "run_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "flags_file": os.path.basename(a.flags),
        "flags_sha256": sha256(a.flags),
        "leie_file": os.path.basename(a.leie),
        "leie_sha256": sha256(a.leie),
        "data_year": a.year,
        "flagged_providers_on_leie": int(len(m)),
        "excluded_before_data_year": int((m["excl_date"] < start).sum()),
        "excluded_during_data_year": int(((m["excl_date"] >= start) & (m["excl_date"] <= end)).sum()),
        "excluded_after_data_year": int(after.sum()),
        "by_exclusion_type": {k: {"heading": HEADINGS.get(k, k), "providers": int(v),
                                  "excluded_after_data_year": int(by_type_after.get(k, 0))}
                              for k, v in sorted(by_type.items(), key=lambda kv: -kv[1])},
        "by_group": groups,
        "python": ".".join(map(str, __import__("sys").version_info[:3])),
        "pandas": pd.__version__,
    }
    os.makedirs(a.out, exist_ok=True)
    with open(os.path.join(a.out, "leie_reasons.json"), "w") as fh:
        json.dump(out, fh, indent=2)

    lines = [
        "# Exclusion reasons for flagged providers on the HHS-OIG LEIE",
        "",
        f"Flags file fingerprint (SHA-256): {out['flags_sha256']}",
        f"LEIE file fingerprint (SHA-256): {out['leie_sha256']}",
        f"Data year: {a.year}. Counts only; no identifiers are written.",
        "",
        f"Flagged providers on the LEIE: {out['flagged_providers_on_leie']} "
        f"(excluded before the data year {out['excluded_before_data_year']}, during {out['excluded_during_data_year']}, "
        f"after {out['excluded_after_data_year']}).",
        "",
        "| Exclusion authority (42 U.S.C. 1320a-7) | Providers | Excluded after the data year |",
        "|---|---|---|",
    ]
    for k, v in out["by_exclusion_type"].items():
        lines.append(f"| {v['heading']} | {v['providers']} | {v['excluded_after_data_year']} |")
    lines += ["", "| Group | Providers | Excluded after the data year |", "|---|---|---|"]
    for g, v in groups.items():
        lines.append(f"| {g} | {v['providers']} | {v['excluded_after_data_year']} |")
    lines += ["", "Appearing on the LEIE is an external signal, not proof about any flagged payment."]
    with open(os.path.join(a.out, "leie_reasons.md"), "w") as fh:
        fh.write("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
