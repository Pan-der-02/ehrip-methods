# E-HRIP Explainable Claim-Flagging Methods

Transparent methods that flag healthcare claims for human review and state the reason for every flag.
Part of the Explainable Healthcare Risk Intelligence Platform (E-HRIP) research project.

**Status:** research prototype, version 0.2.0. Tested only on CMS public synthetic data; not run on real claims.

## What it does

The pipeline reads standard claim fields (beneficiary, provider, service dates, payment amount, and a
service or diagnosis code), rolls claim lines up to one row per claim, and applies three rules:

| Rule | What it flags | Example reason |
|---|---|---|
| `possible_duplicate` | Same beneficiary, provider, service dates, and payment amount as another claim | "Same beneficiary, provider, service dates and payment amount ($182.40) as claim 100231; 2 matching claims in total." |
| `date_order` | Service end date earlier than start date | "Service end date (2022-03-01) is earlier than start date (2022-03-04)." |
| `amount_outlier` | Payment far above comparable claims with the same code, using a robust z-score (median and median absolute deviation) | "Payment $1,450.00 is 8.1x the median ($179.00) for comparable claims with code 99214 (robust z = 14.2)." |

Each flag prioritizes a claim for review. **It does not decide that a payment is improper or fraudulent.**

## How it is tested

Because synthetic data has no known real errors, the pipeline runs a controlled test: it plants known
problems (duplicates, inflated payments, date-order errors) in a copy of the data and measures how many
it finds again (recall). It compares that recall with what flagging the same number of claims at random
would achieve. Every run records its input file hash, settings, seed, and software versions.

Flags on unplanted claims are reported but not scored, so precision and false-positive rates are **not**
measured. Real-world performance can only be evaluated with an organization's data and approval.

## Results on CMS synthetic claims (November 7, 2026)

Run on the CMS synthetic carrier file: 90,705 claims from 1,121,004 claim lines (input SHA-256
`ff63b207...3dffce`, seed 20260101, version 0.2.0). Full run record: [`results/2026-11-07_carrier/`](results/2026-11-07_carrier/).

| Planted problem | Planted | Found | Recall | Expected recall from random selection |
|---|---|---|---|---|
| Duplicate claim | 200 | 200 | 100% | 0.22% |
| Inflated payment (x8) | 200 | 195 | 97.5% | 0.33% |
| Date-order error | 200 | 200 | 100% | 0.22% |

On the original data, the rules flagged 107 claims, all through the amount rule. In this version the amount rule
compares total claim payments grouped by the code on each claim's first line; carrier claims average about 12
lines, so a flag can reflect a claim with many lines. Line-level comparison is the planned next step.

## Data

[CMS Synthetic Medicare Enrollment, Fee-for-Service Claims, and Prescription Drug Event Data](https://data.cms.gov/collection/synthetic-medicare-enrollment-fee-for-service-claims-and-prescription-drug-event)
(Synthetic Medicare Claims PUFs). The files are pipe-delimited and contain no real beneficiary information.
Use `carrier.csv` or `outpatient.csv`. Data files are not included in this repository.

## Run it

```
pip install -r requirements.txt
python ehrip_prototype.py --input carrier.csv --out run_carrier
python ehrip_prototype.py --demo --out demo_run    # toy data, code test only
```

If your file's column names differ, pass a JSON map, for example `{"pay_amt": "LINE_NCH_PMT_AMT"}`, with `--map`.

Each run writes `flags.csv` (one row per flag, with its reason), `run_manifest.json` (settings, hashes,
versions, test results), and `RUN_NOTE.md` (a readable summary).

## Limitations

- Synthetic data only. Results show the method works as designed on planted cases, not real-world accuracy or savings.
- Claims data cannot show whether supporting medical records exist. The methods prioritize claims for documentation review; they do not detect missing documentation directly.
- Rule thresholds are starting values and would need tuning on real data.

## License and citation

MIT License (see `LICENSE`). Citation details are in `CITATION.cff`.

Author: Temiloluwa Emmanuel Olabiyi
