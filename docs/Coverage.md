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

## Optional rent-regulation pack

The `rent-regulation` pack is optional and currently reports **partial coverage**.
Its installable modules are NYC Administrative Code §§ 26-501 through 26-520
(the NYC Rent Stabilization Law) from the reviewed AmLegal bulk XML publication,
and a curated set of official HCR guidance pages covering the overview, leases,
rent increases/overcharge, succession, and essential services.

The current complete Rent Stabilization Code, a reviewed ETPA publication, and
reviewed editions of DHCR fact sheets and operational bulletins are not yet pack
modules. Search and answer responses for recognized rent-regulation questions
include a machine-readable `source_pack_notices` entry identifying unavailable
and unresolved modules. The pack does not determine an apartment's regulatory
status, calculate lawful rent, establish historical applicability, or supply
case-law treatment.

Use `nyc-housing packs list --json` to inspect catalog status and limitations.
Downloads occur only after an explicit install, update, or check action. Pack
removal retains source versions for restoration but removes the modules from the
active retrieval generation.

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

## Saved research and source comparisons

Research matters store only results a user explicitly saves. Each saved item is
an immutable JSON snapshot with a content hash and retained evidence identifiers;
matter notes remain editable. Matter exports include machine-readable and
Markdown views plus checksums. Deleting a matter is preview-first and does not
silently delete a saved item that is linked to another matter.

Source comparison works between retained versions of the same official module.
It aligns stable citations or chunk identifiers, distinguishes text changes from
metadata/parser-only changes, produces local unified diffs, and identifies saved
items that reference changed or removed chunks. It is not a legal citator and
does not decide the legal effect of a source change.

## User documents and extraction

Private resources may be PDF, DOCX, Markdown, or UTF-8 text. DOCX extraction
reads the final visible view, headings, tables, footnotes, and endnotes without
following external relationships; it rejects macros, unsafe ZIP layouts, XML
entities, and excessive expansion. Tracked deletions and comments are excluded
and reported as warnings.

PDF inspection records which pages have weak or absent embedded text. No OCR
engine or language package has yet passed the required packaging, licensing, and
quality review, so OCR requests are recorded as `ocr_unavailable` instead of
claiming an extraction occurred.

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

Property dossiers require an explicitly confirmed building identity. HPD
violation observations are immutable and retain their acquisition time and
query provenance. Complaint, registration, and DOB panels remain visibly
unavailable until their dataset adapters are independently verified; the app
does not blend them into the violation panel or infer missing facts.

## Language, urgent resources, and fully offline readiness

The interface and urgent-resource cards have English and Spanish catalogs.
Generated answers can request English or Spanish and standard or plain-language
style in the same provider operation; the underlying evidence excerpts,
citations, dates, numbers, and qualifications remain preserved. The Spanish UI
catalog is labeled `bilingual_review_pending`, so it is not represented as a
completed professional translation review.

Urgent-resource suggestions are deterministic local rules, independent of model
generation, and do not determine eligibility. The bundled official NYC links
and phone fields are validated at load time, while the catalog remains labeled
`source_verified_review_pending` until named housing-services and bilingual
reviewers approve it.

Offline mode denies application-managed remote traffic. A local model exception
is limited to one validated literal-loopback response endpoint, with no redirects
or environment-proxy inheritance. Fully local cited answers and a complete HPD
snapshot are reported as blocked until separately reviewed artifacts pass their
declared evaluation/completeness checks. The ordinary live-query cache is not a
bulk snapshot and cannot enable citywide analytics.

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
