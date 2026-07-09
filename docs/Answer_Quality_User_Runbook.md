# Answer Quality User Runbook

This runbook covers the parts of answer-quality hardening that require project
owner decisions, credentials, or domain review.

## Goal

Move from local fake-provider answers to MVP-quality answers that are
production-like, citation-grounded, and reviewed for legal usefulness.

## Step 3: Use The Debug Command Once Implemented

After the debug command is added, use it whenever the web UI returns a weak,
malformed, unsupported, or surprising answer.

Expected command:

```text
uv run python -m app.cli.debug answer \
  --user-email admin@example.com \
  --question "What does the HMC say about heat and hot water?" \
  --limit 5
```

What to inspect:

- Did retrieval return the correct source and citation?
- Does the retrieved text actually contain the rule needed to answer?
- Did the answer provider cite the right chunk?
- Did the answer omit key facts even though they were present in the chunk?
- Is the provider `fake` or a real configured provider?

How to interpret results:

- Good retrieved chunks plus weak answer means answer synthesis needs work.
- Bad retrieved chunks means retrieval, chunking, or embeddings need work.
- No cited chunks means citation validation or provider output failed.
- `llm_provider=fake` means the output is a local deterministic fallback, not
  production-like answer generation.

## Step 4: Choose Whether MVP Uses A Real Answer Provider

You need to decide whether the MVP should use a real LLM-backed answer provider
instead of the fake local provider.

Reasons to use a real provider:

- Better plain-language explanations.
- Better synthesis across multiple retrieved chunks.
- More natural handling of statutory subdivisions and guidance pages.
- More realistic MVP testing.

Reasons to delay:

- API cost.
- Token-budget management.
- Privacy and data-handling review.
- More provider failure modes to monitor.

Decision needed from you:

- Provider: OpenAI or another OpenAI-compatible provider.
- Model name.
- Whether local development should use the real provider or only staging.
- Whether questions and retrieved public-source context can be sent to that
  provider under your privacy requirements.

## Step 5: Configure Credentials

I can wire and test the app against provided credentials, but I cannot create an
API key, add billing, or decide which account should pay for usage.

When you are ready, set these values in `.env`:

```text
ANSWER_LLM_PROVIDER=openai
ANSWER_LLM_MODEL=<chosen-model>
ANSWER_LLM_API_KEY=<your-api-key>
ANSWER_LLM_BASE_URL=https://api.openai.com/v1
```

Optional if you also want production-like semantic retrieval:

```text
EMBEDDING_PROVIDER=openai
EMBEDDING_MODEL=<chosen-embedding-model>
EMBEDDING_API_KEY=<your-api-key>
EMBEDDING_BASE_URL=https://api.openai.com/v1
```

After changing embedding settings, regenerate embeddings:

```text
uv run python -m app.cli.embeddings generate
uv run python -m app.cli.embeddings status
```

After changing answer settings, run:

```text
uv run python -m app.cli.evaluate --user-email admin@example.com
```

If you hit the daily token budget during testing, reset local state:

```text
uv run python -m app.cli.limits reset-user --email admin@example.com
```

## Step 6: Validate Legal Adequacy

I can improve grounding, citations, tests, and failure handling. I cannot
certify that an answer is legally adequate for real tenant, owner, attorney, or
advocate use.

You should review answers with a qualified legal/domain reviewer before relying
on them for MVP users.

Recommended review set:

- Owner good-repair duties.
- Heat and hot water.
- HPD complaint reporting.
- HPD enforcement.
- Unsupported case-law questions.
- Questions that require sources not yet ingested.
- Questions with ambiguous or user-specific facts.

For each answer, check:

- Is the direct answer legally accurate?
- Are material exceptions or limits omitted?
- Are citations relevant and sufficient?
- Does the answer avoid legal advice?
- Does the source coverage disclaimer make the corpus limits clear?
- Would a reasonable user misunderstand the answer as complete advice?

## What I Cannot Do For You

- Create or manage paid provider accounts.
- Generate or retrieve your private API keys.
- Approve provider privacy or data-processing terms.
- Decide whether token costs are acceptable.
- Certify legal adequacy.
- Replace review by a qualified attorney or domain expert.

## Minimum Go/No-Go Checklist

Before treating answers as MVP-quality:

- Evaluation passes with required answer terms.
- Debug output shows relevant chunks for key questions.
- Real-provider answers are tested, if a real provider is chosen.
- Rate limits and token budgets are configured intentionally.
- Legal/domain review signs off on the initial question set.
- Unsupported answers remain conservative for out-of-corpus questions.
