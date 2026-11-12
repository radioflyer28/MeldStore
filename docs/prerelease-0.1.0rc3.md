# MeldStore 0.1.0rc3 GitHub prerelease

Published November 12, 2026 with owner approval: [v0.1.0rc3](https://github.com/radioflyer28/MeldStore/releases/tag/v0.1.0rc3).
Release commit: 5318f1fa05980c3af859115fef7c4a56e21d20ad. Both downloaded assets
were verified against the approved build hashes and sizes after publication.

This release adds opt-in PostgreSQL 17 metadata, transaction-scoped bulk SQL,
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

## Artifacts and installation

The published destination is a GitHub prerelease in radioflyer28/MeldStore, tag
v0.1.0rc3, containing meldstore-0.1.0rc3-py3-none-any.whl and
meldstore-0.1.0rc3.tar.gz. Both artifacts are attached to the release linked above.
From a downloaded wheel, in a Python 3.12+ project:

```console
uv add "meldstore[postgres] @ https://github.com/radioflyer28/MeldStore/releases/download/v0.1.0rc3/meldstore-0.1.0rc3-py3-none-any.whl"
```

Both artifact types passed clean core and postgres/all-format installations.
See [deployment qualification](postgresql-verification.md) for exact provider,
server, RustFS and coordinator evidence. CI also covers both SQLite adapters on
Windows/Linux Python 3.12–3.14. Final closeout CI evidence was recorded there before
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

The owner approved this version, GitHub destination and both artifacts on November 12.
No PyPI upload is part of the candidate or this closeout.
