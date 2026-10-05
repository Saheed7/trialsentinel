# Research log

## 2026-10-05: Trial-publication linkage evidence (n = 75 completed trials)
- Sponsor-declared result links (registry_result): 8 trials.
  Indexer-linked (pubmed_si): 29 trials, >3x registry-only recall.
- pubmed_si overlaps registry_derived in 44/48 links (92%): same underlying signal.
- pubmed_tiab: 21 links, 21/21 confirmed by DataBankList, 0 tiab-only (zero marginal recall).
- Linked papers include 5 systematic reviews, 4 meta-analyses, 4 letters, 3 reviews:
  "linked" != "results published".
- Decision: Phase 2 uses typed, date-constrained results-publication evidence.
  Keep tiab pending a larger sample.

  ## 2026-10-05: FAERS disproportionality baseline (snapshot 2026-07-30)
- 66 distinct drug terms from 75 completed trials; first 10 queried.
- Resolution: 7/10 via openFDA harmonised generic_name. After adding a reported-name
  fallback (medicinalproduct): 10/10 resolved (3 via fallback: rosiglitazone,
  vildagliptin, ruboxistaurin mesylate; lower precision, as reporter-entered names).
  With all 10 resolved: 144/231 top-25 pairs (62%) met Evans criteria.
- Evans criteria flagged 113/175 top-25 drug-event pairs (65%): low specificity.
- Positive control recovered: pioglitazone / bladder cancer (PRR 80.4, ROR 95% CI lower 97.1, a = 8,852).
- Dominant false-signal patterns: indication bias (ciclesonide / asthma, wheezing),
  non-clinical terms (therapeutic product effect incomplete), small-count inflation
  (pramlintide / sleep apnoea, a = 5, PRR 53.7).
- Decision: Phase 2 adds indication and non-clinical term filters plus a shrinkage
  statistic before any agent sees a safety signal.
