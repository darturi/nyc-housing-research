# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

People researching New York City housing law and HPD property violations in a private, local workspace. The primary user's exact professional role remains an open decision.

## Product Purpose

NYC Housing Research makes official housing-law sources and bounded HPD violation records searchable from a browser without a hosted account. Success means a user can find the relevant source text, understand where it came from, and distinguish local/free actions from provider-backed or networked actions.

## Positioning

The application keeps its databases, legal-source artifacts, cache, configuration, and usage ledger on the user's machine while still supporting optional provider-backed answers and live HPD lookups with explicit cost and network boundaries.

## Operating Context

The product runs on loopback as a local browser application. Research starts with a question or search terms, then returns cited excerpts from installed public sources. Separate workflows cover HPD property violations, source maintenance, provider settings, spend controls, credentials, usage, and diagnostics.

## Capabilities and Constraints

- Local, keyless source search is free after the corpus is installed.
- Answer mode may send a question and selected excerpts to a configured provider.
- Property lookup contacts NYC Open Data and requires a building identifier or structured address.
- Cost-bearing operations require estimates, approvals, and hard ceilings.
- Loopback access, one-use launch credentials, sessions, CSRF protection, and an optional offline policy are product constraints.
- The product provides general legal information, not legal advice; users must verify current law and sources.

## Brand Commitments

The product name is NYC Housing Research. Its voice is precise, plainspoken, cautious about legal conclusions, and explicit about privacy, provenance, network access, and cost.

## Evidence on Hand

The repository contains real interface copy, official-source metadata, local workspace status, cited search results, HPD result structures, and release-readiness documentation. It does not contain testimonials, customer claims, or a public-facing brand asset system; future work must not fabricate them.

## Product Principles

- Evidence before assertion.
- Local and private by default.
- Make network and cost boundaries visible before action.
- Prefer legible, familiar research workflows over ornamental interaction.
- Preserve the user's ability to verify every meaningful result.

## Accessibility & Inclusion

The browser interface should preserve native semantics, full keyboard access, visible focus, readable contrast, and responsive operation on desktop and narrow screens.
