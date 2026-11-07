# E-HRIP Prototype: Run Note

**Run (UTC):** 2026-11-07T23:14:15+00:00 | **Source:** carrier.csv | **Input SHA-256:** ff63b2070eab6754f8664670b415cbcd65fe759249afa2a7e147eb82563dffce
**Claims analyzed:** 90,705 | **Seed:** 20260101 | **Version:** 0.2.0 | **Python/pandas:** 3.13.18/3.0.5

## Columns used
claim_id = CLM_ID, bene_id = BENE_ID, provider = PRF_PHYSN_NPI, from_dt = CLM_FROM_DT, thru_dt = CLM_THRU_DT, pay_amt = CLM_PMT_AMT, peer_key = HCPCS_CD

## Rules
Every flag carries a plain-language reason. A reviewer makes every decision.
1. **possible_duplicate**: same beneficiary, provider, service dates, and payment amount as another claim.
2. **date_order**: service end date earlier than start date.
3. **amount_outlier**: payment above comparable claims with the same service or diagnosis code (robust z-score on median and median absolute deviation above 6.0; groups of at least 30 claims).

## Controlled test
Known problems were planted in a copy of the data, and the pipeline was scored on finding them again.

| Planted problem | Planted | Found | Recall | Random selection (expected recall) |
|---|---|---|---|---|
| Duplicate claim | 200 | 200 | 1.0 | 0.0022 |
| Inflated payment | 200 | 195 | 0.975 | 0.0033 |
| Date-order error | 200 | 200 | 1.0 | 0.0022 |

The planted duplicates and date-order errors match the rule definitions exactly, so their recall confirms the
rules work as written. The inflated-payment test is the more informative one, because the pipeline has to find
planted payments among naturally varying amounts.

Flags on unplanted claims: 106 of 90,305. Synthetic data has no known real errors, so flags on unplanted claims cannot be scored as right or wrong; precision and false-positive rates are not measured. Random-selection recall is the share of planted cases expected to be caught by flagging the same number of claims at random.

## Limitations
The data is synthetic and contains no known real payment errors. These results show the pipeline runs
reproducibly and finds problems that were deliberately planted. They do not establish real-world accuracy,
precision, false-positive rates, savings, or any conclusion about Medicare beneficiaries. Real-world
evaluation would happen only with an organization's data and approval.
