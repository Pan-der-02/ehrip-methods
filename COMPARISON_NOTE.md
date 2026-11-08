# E-HRIP Comparison with Common Approaches: Run Note

**Run (UTC):** 2026-11-08T03:59:58+00:00 | **Comparison version:** 0.4.0 | **E-HRIP version:** 0.3.1 | **Data year:** 2023
**CMS file:** MUP_PHY_R25_P05_V20_D23_Prov_Svc.csv (SHA-256 c6766dda7c66c74682af892d9ed5f8a347248dc0f6be2bda8aee7eecc02a902b)
**LEIE file:** UPDATED.csv (SHA-256 7483d47bc48734f7955095d51d13dd1111b11cc8a60f54ebe894806e6c293c03)

## Design (fixed before the run)
Every approach selects the same number of providers: the 114,579 flagged by the E-HRIP rule. Data and
eligibility are the same for all approaches (1,175,281 providers). The outcome is whether a selected
provider's NPI appears on the HHS-OIG LEIE (274 of all providers in the data, 0.0233%).

## Results
| Approach | Providers selected | On LEIE | Lift vs all providers | Excluded after data year | Reason per flag |
|---|---|---|---|---|---|
| E-HRIP (robust peer comparison, standardized) | 114,579 | 73 | 2.73x | 71 | Yes |
| Largest billers (total Medicare payment) | 114,579 | 46 | 1.72x | 43 | No |
| Classic outlier score (mean and standard deviation, actual payment) | 114,579 | 63 | 2.36x | 62 | No |
| Random selection (expected) | 114,579 | 26.71 | 1.00x | n/a | No |

## Paired comparison (LEIE providers selected by only one approach)
| E-HRIP compared with | Found only by E-HRIP | Found only by the other | Found by both | One-sided exact sign test p |
|---|---|---|---|---|
| Largest billers (total Medicare payment) | 47 | 20 | 26 | 6.54e-04 |
| Classic outlier score (mean and standard deviation, actual payment) | 18 | 8 | 55 | 0.038 |

## How to read this
All approaches select the same number of providers from the same data, so the counts compare directly. The
LEIE is an external signal, not proof about any selected provider or payment. Only E-HRIP states a reason for
each selection; the other approaches give a ranking or a number.
