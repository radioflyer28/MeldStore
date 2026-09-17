# MVP release preparation

This checklist is for a maintainer preparing MeldStore **0.1.0rc1**. It separates
validated local artifacts from authorization and readiness to publish them.
S09 closes out the existing MVP; it does not add PostgreSQL or distributed
coordination. The latter belong to a future milestone.

## Current gates

- S07/S08 were fast-forwarded into `main` at `368ea9a` after fresh regression and
  successful Windows/Linux/RustFS CI for that exact commit.
- S09 prepares version `0.1.0rc1`, [release notes](../CHANGELOG.md), project links,
  explicit artifact selection and private-artifact exclusion checks.
- The owner approved Apache-2.0 for both repositories. MeldStore now pins
  MeldDB `aad59aba7cb348b7f4e0607962db6ecd5dda8d31`, which packages its license.
  This revision changes licensing/packaging checks, not MeldDB runtime code.
- S09 also includes the requested persistent, verified [file export](file-export.md).
  Move semantics remain deferred.
- The current dependency is an exact Git commit. Git is needed for a fresh
  installation; local/URL artifact installation is distinct from index release.
- No GitHub release, tag, TestPyPI upload, or PyPI upload is authorized or created
  by these preparation steps. Passing CI does not authorize publication.

## Reproduce candidate validation

Local candidate validation on September 17, 2026: Python 3.12.13 / SQLite 3.53.1,
**563 tests passed, 33 expected skips**, clean Ruff, and
versioned wheel/sdist builds. Both artifacts passed fresh core and all-format
installations through both metadata adapters. The dependency lock update changes
only the exact MeldDB revision to its licensed commit, without unrelated upgrades.
The 33 skips are the 27 separately passed live S3 cases and six Windows symlink
permission cases. See [S09 verification](s09-verification.md). Candidate CI must
also pass before its merge.

Use a patched SQLite runtime; check the [runtime policy](sqlite.md), not just
the Python version. Run from a clean source checkout. The version-specific
directory keeps previous development artifacts intact.

```console
uv sync --frozen --all-extras
uv run --frozen --all-extras pytest
uv run --frozen --extra test ruff check .
uv build --out-dir dist/0.1.0rc1
uv run --frozen python tools/package_smoke.py --dist-dir dist/0.1.0rc1
uv run --frozen python tools/package_smoke.py --dist-dir dist/0.1.0rc1 --formats
uv run --frozen --all-extras python tools/s3_lab.py
```

The directory must contain exactly the candidate wheel and source distribution,
not a mixture of versions. Smoke tests check the installed version, license
metadata/file, both metadata adapters and all optional formats. They run outside
the checkout. Source distributions include examples; wheels do not. Archive
checks reject private binary inputs, database journals, environment files,
Git internals and local handoffs. These checks complement review of the staged
diff; they are not a general secret scanner.

The RustFS command uses only generated synthetic fixtures, disposable loopback
resources and ephemeral credentials. Never supply private-data flags in CI.
The 100-file S08 reports remain prior operational evidence; packaging-only
changes do not imply a new large-data measurement.

## Resolve dependency distribution before publication

MeldDB licensing is resolved at the exact pinned revision. Fresh candidate
installations check both packages' Apache-2.0 metadata and packaged license files.

For a PyPI release, arrange an approved MeldDB release on the intended index,
replace the direct Git requirement with the tested version requirement, refresh
the lockfile without unrelated upgrades, then repeat fresh installs without
checkout-only dependency overrides. PyPI does not accept direct-URL dependency
declarations; local installers do. See the
[setuptools dependency guidance](https://setuptools.pypa.io/en/latest/userguide/dependency_management.html#direct-url-dependencies).
Do not substitute an unrelated same-named package from an index.

A GitHub-only artifact distribution can retain the tested Git requirement, but
still needs clear installation requirements and
explicit publication approval. The intended channel must be agreed before
claiming release readiness. No dependency source change or package upload is
silently implied by the release-candidate version bump.

## Final release checkpoint

1. Record the exact candidate commit, dependency revision/source and artifact
   filenames; verify the intended install path from fresh environments.
2. Require green Windows/Linux and RustFS CI for the candidate. Keep prior
   qualification scope and remaining limitations explicit in the release notes.
3. Review archive contents and the proposed Git history. Never publish private
   Parquet inputs, real identifiers, source paths, credentials or handoff history.
4. Merge the approved preparation changes without overwriting concurrent work.
5. Ask the owner to approve the version, destination and actual publication.
   Only then tag or publish; verify the published artifacts afterward.

Until those gates are satisfied, artifacts are **unreleased local candidates**,
not a completed package release.
