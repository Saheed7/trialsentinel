# Claim-grounding labelling protocol (v1)

A claim is labelled against its cited evidence only; outside knowledge is never used.

- **yes**: every part of the claim is stated or paraphrased in the cited evidence.
- **no**: any part is missing from, or contradicted by, the cited evidence.
- **skip**: undecidable within ~1 minute; excluded from scoring.

Decision rules:
1. Dates must appear in the cited evidence. A claim adding "as of <date>" to evidence
   that does not contain that date is **no**.
2. A primary completion date is not a completion date. "Completed as of <primary
   completion date>" is **no**; "primary completion date was <date>" is **yes**.
3. "Linked" is not "published" or "reported the results". Claiming a paper reports or
   publishes the trial requires evidence saying so; a link alone is **no**.
4. Comparisons must match the direction stated in the evidence ("more than", "less than").
5. Paraphrase is acceptable when the meaning is identical ("results posted: no" ->
   "no results have been posted").
