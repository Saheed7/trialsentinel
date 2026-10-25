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

  ## 2026-10-24: FDAAA 801 funnel (rule v1, as-of 2026-10-05, n = 147)
- Stale inputs: 72/147 records lacked parser-v3 fields until re-synced by NCT ID;
  re-sync moved likely_act 12 -> 24 and undetermined 37 -> 22.
- Applicability: 24 likely_act (8 high-confidence, 16 legacy heuristic),
  22 undetermined, 101 not_act (not interventional 29; not FDA-regulated 24;
  no product intervention 18; pre-FDAAA 16; phase 1 drug 14).
- Findings: 6 results_overdue, 5 results_reported_late; 10/11 industry-sponsored.
- Funnel: 69 naive candidates -> 3 likely-ACT overdue -> 0 with deadlines in the
  Final Rule period. All 3 require an approval-status check (pre-2017 statute).
- 3/6 overdue trials fall outside the naive pool (linked paper or not COMPLETED):
  journal publication != registry compliance.
- Decisions: Phase 2b adds a Drugs@FDA approval lookup for pre-Final-Rule
  findings; future cohorts sampled by primary completion >= 2018.

## 2026-10-24: Local LLM structured extraction (4-case smoke eval, Quadro P2000 4 GB)
- Hardware: qwen2.5:3b runs 100% GPU (25.9 tok/s); 7-8B models split ~55/45 CPU/GPU (7.5-8.4 tok/s).
- Without instructions in the prompt, json_schema (constrained decoding) produced valid but
  semantically wrong output (doses kept, "control group" listed as a drug): constraint
  guarantees format, not meaning.
- With a shared system prompt: llama3.1:8b + json_schema 4/4 in 12.1 s with 349 input tokens,
  vs function_calling 4/4 in 27.0 s with 1041 tokens (2.2x faster, 3x fewer tokens).
- Failure modes: qwen2.5:7b failed loudly (schema error) where llama3.1:8b failed silently
  ("placebo" as a drug); a deterministic guardrail corrected the silent case (3/4 -> 4/4).
- An empty-list schema default turned some loud failures into silent empty answers:
  empty extractions from non-placebo text must be treated as abstentions.
- Caveat: n = 4; adding a system prompt moved qwen2.5:7b from 4/4 to 3/4. The default
  (llama3.1:8b, json_schema) is provisional pending a labelled ~50-case evaluation.


## 2026-10-25: Hybrid retrieval, known-item evaluation (n = 67 queries)
- Corpus: 147 trials + 247 publications -> 739 chunks (1,200 chars, 200 overlap);
  bge-small-en-v1.5 (384d) + BM25 sparse (IDF), local CPU, indexed in 89 s.
- Labels from link provenance (registry_result, pubmed_si): query = trial title,
  relevant = papers indexed as reporting that trial.
- hit@1 / hit@5 / MRR@10: dense .791 / .955 / .862; sparse .821 / .925 / .875;
  hybrid RRF .806 / .955 / .875 (~20 ms); hybrid + MS MARCO rerank .806 / .955 / .867 (3.3 s).
- Hybrid matches the best single method on each metric at no latency cost; the
  general-domain reranker adds no quality at 165x latency.
- Caveats: one query = 1.5 points; title-to-paper is an easy task. Question-level
  retrieval quality must be measured separately.
