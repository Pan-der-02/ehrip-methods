"""
cert_overlap.py  -  where provider-level flags fall, against CMS CERT Table L1

Reads the provider-level flags table written by ehrip_provider.py and reports,
for each Part B service CMS lists in Table L1 of its 2025 Medicare Fee-for-Service
Supplemental Improper Payment Data ("Top 20 Service-Specific Overpayment Rates:
Part B"), how many flagged records, providers, and flagged Medicare dollars fall in
that service. It also reports flagged skin-substitute codes (Q4100-Q4399).

Output contains counts and dollar totals only. No NPIs, names, or other identifiers.

Source of the CMS figures (transcribed below):
  CMS, 2025 Medicare Fee-for-Service Supplemental Improper Payment Data, Table L1 (p. 67).
  https://www.cms.gov/files/document/nov-2025-medicare-ffs-supplemental-improper-payment-data-2025922.pdf

Usage:
    python cert_overlap.py --flags provider_flags.csv --out run_cert_overlap
"""
import argparse
import hashlib
import json
import os
import re

import pandas as pd

# code: (CMS label, claims reviewed, overpayment rate %, projected dollars overpaid)
CERT_L1 = {
    "99233": ("Sbsq hosp ip/obs high 50", 334, 22.1, 490503977),
    "99214": ("Office o/p est mod 30 min", 450, 5.0, 459838842),
    "97110": ("Therapeutic exercises", 214, 25.2, 286653227),
    "99215": ("Office o/p est hi 40 min", 226, 14.8, 245231199),
    "99490": ("Chrnc care mgmt staff 1st 20", 68, 65.2, 205392699),
    "99223": ("1st hosp ip/obs high 75", 282, 13.1, 182122220),
    "97530": ("Therapeutic activities", 245, 18.1, 179101995),
    "99285": ("Emergency dept visit hi mdm", 70, 14.3, 164273212),
    "A0427": ("ALS1-emergency", 68, 10.4, 156804531),
    "G0439": ("Ppps, subseq visit", 194, 13.6, 152839912),
    "97140": ("Manual therapy 1/> regions", 223, 30.5, 145990543),
    "99291": ("Critical care first hour", 156, 15.1, 134136940),
    "97112": ("Neuromuscular reeducation", 280, 17.3, 122509052),
    "81479": ("Unlisted molecular pathology", 367, 26.0, 114983372),
    "A0429": ("BLS-emergency", 68, 12.2, 107645005),
    "66984": ("Xcapsl ctrc rmvl w/o ecp", 211, 5.6, 92578007),
    "A0425": ("Ground mileage", 204, 12.4, 90703075),
    "A0428": ("BLS", 131, 12.8, 89122101),
    "99204": ("Office o/p new mod 45 min", 69, 5.4, 86479262),
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
    ap.add_argument("--out", required=True)
    a = ap.parse_args()

    f = pd.read_csv(a.flags, dtype={"npi": str, "hcpcs": str}, usecols=["npi", "hcpcs", "paid"])
    f["hcpcs"] = f["hcpcs"].str.strip()
    total_paid = float(f["paid"].sum())
    rank = {c: i + 1 for i, c in enumerate(f.groupby("hcpcs")["paid"].sum().sort_values(ascending=False).index)}

    rows = []
    for code, (label, claims, rate, overpaid) in CERT_L1.items():
        s = f[f["hcpcs"] == code]
        rows.append({
            "hcpcs": code, "cms_label": label, "cms_claims_reviewed": claims,
            "cms_overpayment_rate_pct": rate, "cms_projected_overpaid": overpaid,
            "flagged_records": int(len(s)), "flagged_providers": int(s["npi"].nunique()),
            "flagged_paid": round(float(s["paid"].sum()), 2),
            "rank_by_flagged_paid": rank.get(code),
        })
    m = f[f["hcpcs"].isin(CERT_L1)]
    q = f[f["hcpcs"].str.match(r"^Q4[1-3]\d\d$", na=False)]

    out = {
        "flags_file": os.path.basename(a.flags), "flags_sha256": sha256(a.flags),
        "cms_source": "CMS 2025 Medicare FFS Supplemental Improper Payment Data, Table L1 (p. 67)",
        "flagged_records_total": int(len(f)), "flagged_providers_total": int(f["npi"].nunique()),
        "flagged_paid_total": round(total_paid, 2),
        "table_l1_services_named": len(CERT_L1),
        "table_l1_services_with_flags": int(sum(r["flagged_records"] > 0 for r in rows)),
        "in_table_l1_services": {
            "records": int(len(m)), "providers": int(m["npi"].nunique()),
            "flagged_paid": round(float(m["paid"].sum()), 2),
            "share_of_flagged_paid_pct": round(float(m["paid"].sum()) / total_paid * 100, 1),
        },
        "skin_substitutes_Q4100_Q4399": {
            "records": int(len(q)), "providers": int(q["npi"].nunique()),
            "flagged_paid": round(float(q["paid"].sum()), 2),
            "codes": sorted(q["hcpcs"].unique().tolist()),
        },
        "by_service": sorted(rows, key=lambda r: -r["flagged_paid"]),
    }
    os.makedirs(a.out, exist_ok=True)
    with open(os.path.join(a.out, "cert_overlap.json"), "w") as fh:
        json.dump(out, fh, indent=2)

    L = [
        "# Where the flags fall: CMS CERT Table L1 services",
        "",
        f"Flags file fingerprint (SHA-256): {out['flags_sha256']}",
        "CMS figures: 2025 Medicare FFS Supplemental Improper Payment Data, Table L1 (p. 67). Counts and totals only; no identifiers.",
        "",
        f"Table L1 names {out['table_l1_services_named']} Part B services; {out['table_l1_services_with_flags']} appear among the flagged records.",
        f"Flagged records in those services: {out['in_table_l1_services']['records']:,} records, "
        f"{out['in_table_l1_services']['providers']:,} providers, ${out['in_table_l1_services']['flagged_paid']:,.0f} "
        f"({out['in_table_l1_services']['share_of_flagged_paid_pct']}% of ${total_paid:,.0f} flagged).",
        f"Skin substitutes (Q4100-Q4399): {out['skin_substitutes_Q4100_Q4399']['records']} records, "
        f"{out['skin_substitutes_Q4100_Q4399']['providers']} providers, ${out['skin_substitutes_Q4100_Q4399']['flagged_paid']:,.0f}.",
        "",
        "| HCPCS | CMS label | CMS overpayment rate | CMS projected overpaid | Flagged records | Flagged providers | Flagged paid | Rank by flagged paid |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in out["by_service"]:
        L.append(f"| {r['hcpcs']} | {r['cms_label']} | {r['cms_overpayment_rate_pct']}% | ${r['cms_projected_overpaid']:,} | "
                 f"{r['flagged_records']:,} | {r['flagged_providers']:,} | ${r['flagged_paid']:,.0f} | {r['rank_by_flagged_paid'] or '-'} |")
    L += ["", "A flag directs review; it is not a finding that any payment was improper."]
    with open(os.path.join(a.out, "cert_overlap.md"), "w") as fh:
        fh.write("\n".join(L) + "\n")
    print("\n".join(L))


if __name__ == "__main__":
    main()
