# Coverage and limitations

## Core legal collection

The L1 source manifest defines five independently versioned modules:

| Module | Publisher | Included scope |
| --- | --- | --- |
| NYC Housing Maintenance Code | NYC Council / American Legal Publishing | HMC sections in official Administrative Code XML |
| New York Multiple Dwelling Law | New York State Senate | Official MDL publication |
| RPAPL | New York State Senate | Official Real Property Actions and Proceedings Law publication |
| Real Property Law Good Cause provisions | New York State Senate | Article 6-A sections 210–216 and section 231-c notice requirements |
| HPD tenant/owner guidance | NYC HPD | Curated complaint, follow-up, clearing, certification, and enforcement pages |

The source manifest records publisher URLs, parser/profile version, scope,
required anchors, content hash, fetch/check times, and redistribution review
state. Run `nyc-housing sources --json` for the exact active installation.

The package does not ship the legal texts. Each user downloads them from official
publishers unless they explicitly import a separately reviewed canonical bundle.
This avoids making unresolved redistribution assumptions and keeps freshness in
the user's control.

## What legal search and answers do not cover

- No case-law, citator, docket, treatise, commercial headnote, or paywalled source.
- No guarantee that a source is the most current until a source check succeeds.
- No individualized legal advice, outcome prediction, attorney-client relationship,
  rent-stabilization determination, Good Cause eligibility determination, or
  building legal-regime classification.
- A cited answer is constrained to the installed generation and supplied excerpts;
  it can still be incomplete or wrong. Read the evidence and original link.
- Missing semantic vectors do not mean missing legal text. Search falls back to
  exact citations and FTS5 and labels the semantic status.

## HPD property data

L1 performs typed, filtered calls to NYC Open Data dataset `wvxf-dwi5` (Housing
Maintenance Code Violations). The local connector projects only allowlisted
fields, supports building/registration ID or structured address resolution,
returns candidates for ambiguity, and paginates in a deterministic order.

Important limitations:

- No matching violation row is not proof that a building does not exist.
- Pages fetched at different times are not a transactionally consistent snapshot.
- `open` maps to the source's `Open` value and `closed` maps to `Close`; original
  `currentstatus`, `currentstatusdate`, and `violationstatus` values are preserved
  in records and exports.
- Interactive filters include inclusive inspection-date bounds. Results record
  acquisition start/end; source-update time and exact total remain null unless
  obtained authoritatively for the identical filter.
- BBL lookup, global counts, citywide aggregates, trend analytics, and complete
  offline HPD research require the optional L2 bulk-data extension.
- First pages contain at most 100 rows. A complete export is capped by pages and
  deadline and remains labeled partial if those bounds are reached.

## Knowledge-base impact of local distribution

Local distribution does not inherently reduce legal knowledge: it preserves full
source text, exact citations, source versions, and optional embeddings. It changes
how data arrives. Users download public legal sources and query HPD live/on cache
instead of relying on a maintainer's roughly 23 GB bulk database. The tradeoff is
that separate installations may be on different source generations, and offline
property knowledge is limited to each user's cache unless L2 bulk data is added.

The five-module corpus is small enough for ordinary local storage. The large HPD
table is kept remote and queried narrowly, which is what makes the no-hosting L1
design feasible without sacrificing the core legal RAG knowledge base.

A bounded live verification on 2026-09-14 installed 741 chunks and occupied
about 70.3 MiB before embeddings. The observed distribution was 153 HMC, 193
MDL, 365 RPAPL, 8 Good Cause, and 22 HPD-guidance chunks. Counts and bytes may
change when publishers update their materials; manifests and generation records
make those changes visible rather than treating 741 as a permanent target.

The package also carries a 55-case deterministic retrieval fixture and the prior
28-question legal-review set with expected citations, qualifications, missing
facts, property behavior, and refusal cases. The latter remains explicitly
`domain_review_required`; executable structure is not a substitute for a housing-law
reviewer's approval.
