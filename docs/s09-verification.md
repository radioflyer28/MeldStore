# S09 release preparation and file export

Local verification: September 17, 2026, Windows, Python 3.12.13 / SQLite 3.53.1.
Artifacts remain unreleased `0.1.0rc1` candidates. Publication requires separate
approval and an agreed distribution channel; see [release gates](release.md).

## Changes and evidence

- `Store.export_file(id, destination)` persistently copies verified raw bytes
  without overwriting destinations, deleting sources, or modifying catalog rows.
  Both metadata adapters cover empty/binary files, independent destination edits,
  missing IDs, transaction rejection, managed-path protection, corrupt source/copy,
  disk/publication errors, competing exporters, and process exits on either side
  of filename publication. NPZ export verifies exact encoded bytes without decoding.
- The full all-extra suite passed **563 tests, 33 skips**. Skips comprise 27 live
  S3 cases run separately and six Windows symlink-permission cases.
- The disposable pinned RustFS lab passed **27 live tests**, including export
  through both adapters, conditional/multipart capability checks and outage recovery.
  Its temporary container and volumes were removed by the lab runner.
- Existing 128 MiB local subprocess probes now also export and compare bytes
  with bounded reads; both pass their memory limit in the regression suite.
- Remote fresh-process probes import, materialize and export synthetic 32/256 MiB
  files. Additional peak RSS was 16,117,760 / 16,179,200 bytes; export plus byte
  comparison took 0.47 / 3.54 seconds. These are local-lab observations, not S3
  service-level promises or standalone export-only memory measurements.
- Ruff passed. uv-built wheel and sdist passed fresh core and all-format installs,
  export/lifecycle/backup checks through both adapters, archive privacy guards,
  and Apache-2.0 metadata/license-file checks for both packages.
- MeldDB is pinned to `aad59aba7cb348b7f4e0607962db6ecd5dda8d31`. Its runtime
  source is unchanged from the previous pin. Its own tests passed 246 cases
  with one skip, lint/build passed, and isolated wheel/sdist qualification passed
  the same suite and shipped examples. Its [license CI](https://github.com/radioflyer28/melddb/actions/runs/35241463712)
  passed. No unrelated dependency versions changed in MeldStore's lockfile.

Commands use uv for dependency resolution, testing and packaging, as documented
in the release checklist. The initial S09 packaging-only commit also passed
[CI](https://github.com/radioflyer28/MeldStore/actions/runs/35240184398);
that run predates export and is not export's cross-platform evidence.

## Boundaries

Export requires a hard-link-capable local destination filesystem and existing
parent directory. It is create-only, not an atomic cross-system move. Crash
tests do not certify power-loss durability. See the [file contract](file-export.md).

No private payloads, private paths, real identifiers or handoff history are part
of this change. S08's 100-file private workload was not rerun for export. All new
qualification uses synthetic data. PostgreSQL metadata, multi-host coordination,
AWS/Garage qualification and move semantics remain deferred.
