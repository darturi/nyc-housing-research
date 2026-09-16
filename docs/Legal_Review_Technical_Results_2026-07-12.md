# Legal Review Technical Results

Date: 2026-07-12

Historical hosted-system result. It is retained as migration/comparison evidence;
it is not an acceptance result for the current local release candidate. See the
current L1 acceptance report and rerun the packaged answer evaluator for current
profile evidence.

## Run Configuration

- Answer provider: OpenAI `gpt-5.3-chat-latest`
- Embedding provider: OpenAI `text-embedding-3-small`
- Corpus: 680 embedded legal and guidance chunks
- Property data: 5,000 HPD violation records

## Summary

- Questions run: 28
- Answered: 20
- Unsupported: 6
- Property lookups: 2
- Provider or routing errors: 0
- Internal chunk IDs leaked into answer prose: 0

## Results

| # | Question | Result | Citations or property matches |
| --- | --- | --- | --- |
| 1 | Owner good-repair duties | Answered | NYC Admin Code sections 27-2005, 27-2006, 27-2045 |
| 2 | Heat during NYC heat season | Answered | NYC Admin Code section 27-2029 |
| 3 | Required daytime and overnight heat temperatures | Answered | NYC Admin Code section 27-2029 |
| 4 | Hot-water hours and temperature | Answered | NYC Admin Code section 27-2031 |
| 5 | Gas or electric alternatives to central systems | Answered | NYC Admin Code sections 27-2028, 27-2031, 27-2032 |
| 6 | RPAPL section 711 | Answered | RPAPL section 711 |
| 7 | Notice before nonpayment proceeding | Unsupported | No citation |
| 8 | Holdover proceeding | Answered | RPAPL sections 721, 753, 751 |
| 9 | Tenant definition under RPAPL section 711 | Answered | RPAPL section 711 |
| 10 | Tenant versus squatter | Answered | RPAPL section 711 |
| 11 | Removal without a court proceeding | Answered | RPAPL sections 711, 713 |
| 12 | Multiple Dwelling Law section 78 | Answered | Multiple Dwelling Law section 78 |
| 13 | Unsafe buildings, repairs, or evacuation | Answered | Multiple Dwelling Law sections 78, 11; NYC Admin Code section 27-2139 |
| 14 | HMC building maintenance | Answered | NYC Admin Code sections 27-2005, 27-2002, 27-2012 |
| 15 | Rent accepted after a holdover starts | Unsupported | No citation |
| 16 | Heat and hot-water exceptions | Answered | NYC Admin Code sections 27-2029, 27-2031; Multiple Dwelling Law section 79 |
| 17 | Reporting an HPD complaint | Answered | HPD Tenant and Owner Guidance |
| 18 | What happens after an HPD complaint | Unsupported | HPD Tenant and Owner Guidance |
| 19 | HPD enforcement information | Answered | NYC Admin Code section 27-2153 |
| 20 | Clearing violations or using eCertification | Answered | NYC Admin Code section 27-2153 |
| 21 | Violations at 22 FRONT STAGG STREET | Property route, zero matches | 0 |
| 22 | Violations at 999999 NO SUCH STREET | Property route, zero matches | 0 |
| 23 | No heat today | Answered | NYC Admin Code section 27-2029; Multiple Dwelling Law section 79 |
| 24 | One month behind on rent | Answered | RPAPL sections 711, 755 |
| 25 | Good Cause Eviction applicability | Unsupported | No citation |
| 26 | Unpublished housing case | Unsupported | No citation |
| 27 | Whether the user will win a Housing Court case | Answered, scope-limited | RPAPL sections 745, 776, 743 |
| 28 | Rights under an unprovided lease clause | Unsupported | No citation |

## Review Priorities

1. Review question 7. It was unsupported even though question 24 returned a
   14-day rent-demand rule. This is a retrieval or answer-synthesis
   consistency issue.
2. Review questions 18 through 20. The current HPD guidance corpus is too thin
   for post-complaint and eCertification detail; questions 19 and 20 fell back
   to Alternative Enforcement Program law rather than the intended HPD guidance.
3. Investigate question 21. The expected positive property lookup returned zero
   matches, so the locally loaded HPD dataset, address normalization, and test
   sample should be reconciled.
4. Have a legal reviewer assess questions 23, 24, and 27 for proper
   qualification and avoidance of individualized legal advice.
5. Treat questions 25, 26, and 28 as expected corpus boundaries unless new
   official sources are intentionally added.

## Recommended Follow-Up

Use the debug command for questions 7, 18 through 21, 24, and 27 to inspect
retrieval ranks, cited chunks, and source coverage before changing prompts or
adding corpus material.

```bash
uv run nyc-housing debug answer \
  --question "<review question>" --limit 5 --json
```
