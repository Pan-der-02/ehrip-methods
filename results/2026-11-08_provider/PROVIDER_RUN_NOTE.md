# E-HRIP Provider-Level Run Note

**Run (UTC):** 2026-11-08T02:59:23+00:00 | **Version:** 0.3.1 | **Data year:** 2023
**CMS file:** MUP_PHY_R25_P05_V20_D23_Prov_Svc.csv (SHA-256 c6766dda7c66c74682af892d9ed5f8a347248dc0f6be2bda8aee7eecc02a902b)
**LEIE file:** UPDATED.csv (SHA-256 7483d47bc48734f7955095d51d13dd1111b11cc8a60f54ebe894806e6c293c03)

## Rule (fixed before analysis)
For each provider, service code (HCPCS), and place of service, the standardized Medicare payment per
beneficiary is compared with all providers billing the same code in the same place of service. A row is
flagged when its robust z-score (median and median absolute deviation) is above 6.0,
in peer groups of at least 30 providers. Standardized amounts remove geographic payment differences.
Peer groups with zero spread (MAD of zero, including zero apart from floating-point rounding) are skipped.

## Version note
Version 0.3.1 corrects one implementation detail of the rule above. Version 0.3.0 skipped a peer group only
when its MAD was exactly 0, so groups whose spread was zero apart from rounding error were used, and tiny
differences in them produced extreme z-scores. Version 0.3.1 treats a MAD below one billionth of the group
median as zero. No other change was made.

## Results
- Rows analyzed: 9,660,647 (1,175,281 providers); Medicare paid on those rows: $93,721,075,813
- Peer groups used: 3,804 (skipped: 4,922 small, 123 zero MAD)
- Rows flagged: 193,387; providers flagged: 114,579
- Medicare paid on flagged rows: $9,036,817,958
- Amount above the peer median for the same beneficiaries: $6,652,796,724

## Exclusion-list check (HHS-OIG LEIE, matched by NPI)
| | Providers | On LEIE | Share |
|---|---|---|---|
| All providers in data | 1,175,281 | 274 | 0.0233% |
| Flagged providers | 114,579 | 73 | 0.0637% |

Lift: 2.73x. Expected on LEIE if flags were random: 26.71. One-sided binomial p-value: 1.22e-13.
Flagged providers on LEIE by exclusion date relative to the data year: before 1, during 1, after 71.

## How to read this
A flag is a reason to review, not a finding that a payment was improper. The data are provider-level
summaries, and legitimate differences in patient mix or specialty can produce high values. The LEIE lists
exclusions currently in effect; appearing on it is an external signal, not proof about any flagged payment.
