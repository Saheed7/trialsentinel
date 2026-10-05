# TrialSentinel

**Verifiable, injection-robust multi-agent auditing of clinical-trial integrity on AWS.**

![CI](https://github.com/Saheed7/trialsentinel/actions/workflows/ci.yml/badge.svg)

TrialSentinel triangulates ClinicalTrials.gov, PubMed, and openFDA (FAERS) to detect
research-integrity signals such as unreported results, outcome switching, and safety
discrepancies. It combines deterministic rule engines with LLM agents (LangGraph on Amazon
Bedrock), claim-level evidence grounding, conformal risk-controlled human review, and
adversarial robustness testing.

> Status: under active development. See the roadmap in `docs/`.

## Local development

    cp .env.example .env
    docker compose up -d
    uv sync
    uv run pytest
    uv run uvicorn trialsentinel.api.main:app --reload

API docs: http://localhost:8000/docs
