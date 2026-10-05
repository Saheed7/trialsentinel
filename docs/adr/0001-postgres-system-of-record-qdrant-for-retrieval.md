# ADR-0001: PostgreSQL as system of record, Qdrant for retrieval

- Status: Accepted
- Date: 2026-10-05

## Context
TrialSentinel needs (a) exact, relational queries over structured registry data
(dates, statuses, sponsors, outcome lists) that rule engines and auditors depend on,
and (b) semantic plus lexical retrieval over unstructured text (summaries, abstracts,
adverse-event narratives) for LLM agents.

## Decision
- PostgreSQL (Amazon RDS in production) is the system of record for all structured
  entities, ingestion lineage, findings, and human review decisions.
- Qdrant is the retrieval index. It stores chunk embeddings plus sparse vectors for
  hybrid (dense + lexical) search, with payload filters (e.g., nct_id, phase, sponsor).
- Qdrant is derived data: it can be fully rebuilt from PostgreSQL plus the raw archive.

## Alternatives considered
- pgvector only: simplest operationally, but hybrid sparse+dense retrieval and
  filtered ANN at scale require more custom work; retrieval load would compete
  with transactional load on one database.
- Amazon OpenSearch Serverless: AWS-native hybrid search, but its minimum capacity
  cost dominates a small deployment.

## Consequences
- Two stores to operate; mitigated by treating Qdrant as rebuildable.
- Enables a retrieval ablation (dense vs. hybrid) for the evaluation study.
