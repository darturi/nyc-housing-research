# Legal Review Question Set

## Purpose

Use this set to assess whether the MVP returns legally useful, source-grounded,
appropriately cautious housing information. Run the questions in three batches
of eight to manage local token usage.

For each response, record:

- Answer status: answered or unsupported
- Whether the direct answer is accurate and complete enough for the question
- Whether every citation supports the proposition for which it is used
- Material exceptions, qualifications, or source gaps that were omitted
- Whether the language avoids individualized legal advice
- Reviewer verdict: pass, needs revision, or unsupported as expected

## Core Law

1. What are an owner's good-repair duties under the HMC?
2. What heat must a landlord provide during NYC heat season?
3. What are the required temperatures during daytime and overnight heat hours?
4. When must a landlord provide hot water, and at what temperature?
5. When can gas or electric systems substitute for central heat or hot water?
6. What does RPAPL section 711 cover?
7. What notice is required before a nonpayment summary proceeding?
8. What is a holdover proceeding, and when may a landlord bring one?

## Definitions And Limits

9. Who qualifies as a tenant under RPAPL section 711?
10. How does RPAPL distinguish a tenant from a squatter?
11. Can a landlord remove a tenant without a court proceeding?
12. What does Multiple Dwelling Law section 78 cover?
13. What legal limits apply when a building is unsafe and needs repairs or
    evacuation?
14. What does the Housing Maintenance Code require concerning building
    maintenance generally?
15. Does accepting rent after a holdover case starts end the case?
16. What exceptions or qualifications apply to heat and hot-water requirements?

## HPD Guidance And Property Lookup

17. How can a tenant report a housing complaint to HPD?
18. What should a tenant expect after reporting an HPD complaint?
19. What HPD enforcement information is available to tenants and owners?
20. How can an owner clear HPD violations or use eCertification?
21. Show HPD violations at 22 FRONT STAGG STREET.
22. Show HPD violations at 999999 NO SUCH STREET.

## Ambiguous And Unsupported Questions

23. My apartment has no heat today. What should I do, and what details matter?
24. My landlord says they can evict me because I am one month behind on rent. Is
    that true?
25. Does Good Cause Eviction apply to my apartment?
26. What did the court hold in an unpublished housing case?
27. Can you tell me whether I will win my Housing Court case?
28. What are my rights under a lease clause that is not included in the source
    materials?

## Expected Behavior

- Questions 1 through 22 should return readable, relevant public-source
  citations when the corpus supports an answer.
- Questions 23 through 25 should provide general legal information, include
  material qualifications, and avoid case-specific conclusions.
- Questions 26 through 28 should return an unsupported or scope-limited
  response rather than speculate.

## Follow-Up For A Weak Result

Use the debug command to separate retrieval problems from answer-generation
problems:

```bash
uv run nyc-housing debug answer \
  --question "<review question>" --limit 5 --json
```

Estimate the whole answer-generation review before approving provider work:

```bash
uv run nyc-housing evaluate --answers --estimate-only --json
uv run nyc-housing evaluate --answers \
  --approve-cost --max-cost-usd 1.00 --json
```

The second command emits the review inputs and outputs but always retains the
`domain_review_required` status. Automated status/source/citation checks are only
a technical screen for a qualified reviewer's proposition-level assessment.
