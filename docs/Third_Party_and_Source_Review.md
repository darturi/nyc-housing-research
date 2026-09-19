# Third-party and source review inventory

Date: 2026-09-14

This is a release-review inventory, not a legal conclusion. The repository's
original code and documentation use Apache-2.0; this inventory records what the
maintainer must still verify before distributing dependencies or source
artifacts. The authoritative texts are the license files shipped by each exact
package/source and the publisher terms in force at publication time.

## Locked Python graph

`uv.lock` resolves 79 packages across core, desktop, credentials, legacy, and development
extras. The direct dependencies and the license expressions/classifiers visible
in the locally installed distribution metadata are:

| Scope | Package | Locked version | Installed-metadata license |
| --- | --- | ---: | --- |
| Core | Alembic | 1.18.5 | MIT |
| Core | FastAPI | 0.139.0 | MIT |
| Core | HTTPX | 0.28.1 | BSD-3-Clause |
| Core | Jinja2 | 3.1.6 | BSD classifier |
| Core | NumPy | 2.5.3 | Compound SPDX expression; inspect bundled notices |
| Core | pydantic-settings | 2.14.2 | MIT |
| Core | pypdf | 6.14.2 | BSD-3-Clause |
| Core | python-dotenv | 1.2.2 | BSD-3-Clause |
| Core | SQLAlchemy | 2.0.51 | MIT |
| Core | Uvicorn | 0.49.0 | BSD-3-Clause |
| Credentials | keyring | 25.7.0 | Verify installed license file |
| Desktop | pywebview | 6.2.1 | BSD-3-Clause |
| Desktop build | py2app | 0.28.10 | MIT or PSF |
| Desktop runtime | PyObjC family | 12.2.2 | Verify bundled license files |
| Legacy/dev | argon2-cffi | 25.1.0 | MIT |
| Legacy/dev | boto3 | 1.43.46 | Apache-2.0 |
| Legacy/dev | psycopg / psycopg-binary | 3.3.4 | LGPL-3.0-only |
| Development | pytest | 9.1.1 | MIT |
| Development | Ruff | 0.15.20 | MIT |
| Development | Playwright | 1.62.0 | Apache-2.0 (installed license file) |
| Development | pyee | 13.0.1 | MIT |
| Development | greenlet | 3.5.3 | MIT with PSF/Stackless-derived portions; inspect bundled notices |

Notable transitive metadata includes Certifi under MPL-2.0, packaging under
`Apache-2.0 OR BSD-2-Clause`, typing-extensions under PSF-2.0, and several
BSD/MIT/Apache packages. This summary is not a substitute for reviewing all 62
locked distributions, their included license/notice files, optional native
wheels, or the dependencies selected when a wheel is installed without the
repository lockfile.

Repeat the exact dependency inventory with:

```bash
uv tree --locked
uv export --locked --all-extras --no-hashes
```

Before publication, produce a machine-readable bill of materials or license
report from the final lock (including the browser binary downloaded only by the
opt-in end-to-end job), inspect every reported license file, resolve unknown
metadata, and retain the report with the release evidence.

## Public information sources

The application downloads rather than republishes the following material:

| Source | Publisher/access path | Current release posture |
| --- | --- | --- |
| NYC Housing Maintenance Code | NYC Council / American Legal Publishing | Public access; redistribution review unresolved |
| Multiple Dwelling Law | New York State Senate | Public access; redistribution review unresolved |
| RPAPL | New York State Senate | Public access; redistribution review unresolved |
| Real Property Law Article 6-A and § 231-c | New York State Senate | Public access; redistribution review unresolved |
| HPD tenant/owner guidance | NYC HPD | Public access; redistribution review unresolved |
| HPD Housing Maintenance Code Violations | NYC Open Data dataset `wvxf-dwi5` | Live filtered access; verify current NYC Open Data terms before redistribution |

The versioned source manifests preserve publisher URLs and mark each legal source
`redistribution_allowed: null`. Therefore the candidate package contains parser
code and manifests, not the downloaded legal corpus or cached HPD records.

## Decisions still required

1. Confirm Apache-2.0 attribution and contributor practices for the final release.
2. Complete a final exact-version dependency-license review and add required
   notices/attributions.
3. Record a source-by-source redistribution decision. Until then, publish no
   downloaded artifacts or prebuilt corpus bundle.
4. Re-run the inventory if `uv.lock`, package extras, source manifests, or the
   release artifact contents change.
