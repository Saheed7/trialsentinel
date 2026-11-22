
## 2026-11-21: Prompt-injection red-team, round 1
Pre-registered in PR #20 before any run. Baseline (v3): 36/36 attacks published a false claim (CI 90-100%). Defended (rule D v1): 0/36 (CI 0-10%). The defences failed both cost criteria (replay on test.csv: 13 more wrongful rejections, 21/212 = 9.9%; clean runs kept 70% of verified claims, floor 75%), so they stay opt-in. Details: docs/research/redteam-round1-results.md; raw data: data/redteam/results.jsonl. Round 2 (rule D v2) is pre-registered in docs/research/redteam-preregistration-round2.md, committed before any round-2 run.
