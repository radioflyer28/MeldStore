# MeldStore 0.1.0rc3 candidate

Status: prepared for GitHub prerelease review; not yet tagged or published.
The published 0.1.0rc2 remains available until publication is explicitly approved.

This candidate adds opt-in PostgreSQL 17 metadata, transaction-scoped bulk SQL,
resumable metadata migrations, explicit single-host PostgreSQL/RustFS binding,
offline SQLite cutover with application-owned SQL mappings, and native whole-store
archive/restore with verified payloads. Existing SQLite adapters remain supported.

The MeldDB dependency remains the exact public Git revision
01e27441d7512f85663c8a86986429d62a69bff9. Git is required for fresh installations.
MeldStore and MeldDB remain Apache-2.0. The postgres extra installs Psycopg;
core installation keeps PostgreSQL imports optional.

Locked optional dependencies are updated to certifi 2026.10.18, msgpack 1.2.3 and
urllib3 2.8.0, resolving the four dependency alerts reported during closeout.
No unrelated dependency upgrade or provider pin change is included.

## Candidate artifacts and installation

The proposed destination is a GitHub prerelease in radioflyer28/MeldStore, tag
v0.1.0rc3, containing meldstore-0.1.0rc3-py3-none-any.whl and
meldstore-0.1.0rc3.tar.gz. These filenames are candidate artifacts, not live links.
From a downloaded candidate wheel, in a Python 3.12+ project:

```console
uv add "meldstore[postgres] @ file:///absolute/path/to/meldstore-0.1.0rc3-py3-none-any.whl"
```

Both artifact types must pass clean core and postgres/all-format installations.
See [deployment qualification](postgresql-verification.md) for exact provider,
server, RustFS and coordinator evidence. CI also covers both SQLite adapters on
Windows/Linux Python 3.12–3.14. Final closeout CI evidence is recorded there before
publication approval is requested.

## Deployment and recovery boundaries

All clients must share one persistent coordinator directory on one host, even
with remote metadata and payload services. External SQL writers must follow the
same exclusion protocol. Preserve the source during offline cutover; verify the
destination before explicitly switching clients. Native recovery requires trusted
archives, explicit PostgreSQL 17 client tools and operator-provisioned roles and
dependencies. See the connection, coordination, cutover and recovery guides linked
from the README.

AWS, Garage, multi-host coordination, live cutover, other PostgreSQL versions and
blanket MeldDB managed-schema portability are not qualified by this release.
XXH3-128 verifies corruption; it is not an authenticity signature. New PostgreSQL
fixtures are synthetic and do not establish large-workload performance results.
No private Parquet files or private filesystem paths are included in artifacts.

Publication requires approval of this version, GitHub destination and artifacts.
No PyPI upload is part of the candidate or this closeout.
