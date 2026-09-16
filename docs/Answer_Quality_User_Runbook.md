# Answer-quality review runbook

Use this runbook for the local-distribution release candidate. It separates
deterministic retrieval evidence, paid answer generation, and substantive legal
review so one cannot be mistaken for another.

## 1. Establish the reviewed input

Run read-only readiness and corpus checks first:

```bash
uv run nyc-housing status --json
uv run nyc-housing corpus verify --json
uv run nyc-housing evaluate --offline --json
```

Record the active corpus generation, source versions/hashes, retrieval metrics,
answer/embedding profile IDs, profile price dates, and application version. The
55-case retrieval evaluation makes no provider call and does not assess generated
answer correctness.

## 2. Inspect one weak question

```bash
uv run nyc-housing debug answer \
  --question "What does the HMC say about heat and hot water?" \
  --limit 5 --json
```

The command uses the same retrieval/provider/ledger path as normal answers and
reports its operation ID, selected profiles, evidence, answer status, and metered
events. It never prints the credential or hidden provider prompt. A `fake-*`
profile is a synthetic interface check, not a legal-quality model result.

Interpret a weak result in this order:

- Wrong or missing evidence indicates a retrieval, coverage, or source problem.
- Good evidence plus an unsupported/weak answer indicates a synthesis or model
  problem.
- `provider_error` indicates credentials, network, provider, timeout, or budget;
  the returned evidence remains usable.
- A valid evidence marker proves only that the cited excerpt was supplied, not
  that it substantively supports each sentence.

## 3. Configure and validate a real profile deliberately

```bash
uv run nyc-housing profiles select answer openai-answer-luna-v1
uv run nyc-housing profiles select embedding openai-embedding-3-small-v1
uv run nyc-housing credentials set openai
uv run nyc-housing credentials validate openai \
  --approve-cost --max-cost-usd 0.000001 --json
```

The credential prompt is hidden. Environment-only configuration is supported via
`NYC_HOUSING_OPENAI_API_KEY`; do not put a key in ordinary settings, a command
argument, an issue, or a review artifact. Validation is a minimal, potentially
billable embedding call recorded by the local ledger.

## 4. Estimate, approve, and run the answer suite

```bash
uv run nyc-housing evaluate --answers --estimate-only --json
uv run nyc-housing evaluate --answers \
  --approve-cost --max-cost-usd 1.00 --json
```

Use a ceiling at least as large as the reported conservative estimate and no
larger than the amount deliberately approved. The configured per-operation and
monthly budgets remain hard limits. The runner uses one traceable operation ID,
stops on provider failure, and reports settled/uncertain local cost deltas.

The 26 legal cases include expected citations or sources, required propositions,
missing facts for personal scenarios, and conservative unsupported behavior. Two
property cases remain in the fixture but are reviewed through the separately
verified property flow. Automated source/citation/status results are a technical
screen only; the report always says `domain_review_required`.

## 5. Perform proposition-level domain review

For every case, a qualified housing-law reviewer records pass, needs revision, or
unsupported as expected after checking:

- direct accuracy and whether every material proposition is supported;
- exceptions, qualifications, source currency, jurisdiction, and missing facts;
- whether each citation supports the sentence for which it is used;
- conservative handling of case law, absent lease text, eligibility questions,
  and outcome predictions;
- whether a reasonable tenant or owner could mistake the answer for complete or
  individualized legal advice.

Prioritize nonpayment notice, complaint follow-up, eCertification, personal heat
and eviction scenarios, and Good Cause coverage. A passing old-system answer or
matching citation string is not sufficient evidence.

## 6. Go/no-go rule

Do not claim legal-answer acceptance until the real-profile run is reproducible
against the recorded corpus/profile versions and every designated must-pass case
has domain-review approval with no unresolved material correctness issue. Provider
account creation, billing approval, privacy-terms approval, and legal certification
remain maintainer/reviewer responsibilities rather than code defaults.
