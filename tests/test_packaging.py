import io
import tarfile
import zipfile

import pytest

from tools.package_smoke import check_archive, release_artifacts


@pytest.mark.parametrize("name", [
    "input.parquet", "INPUT.PARQUET", "array.npz", "array.b2nd", "catalog.db",
    "catalog.db-wal", "catalog.sqlite-shm", ".planning/HANDOFF.json",
    ".qualification/report.json", ".env", ".env.local", ".git/config",
])
def test_archive_guard_rejects_private_artifacts(tmp_path, name):
    archive = tmp_path / "test.whl"
    with zipfile.ZipFile(archive, "w") as output:
        output.writestr(name, b"synthetic fixture")
    with pytest.raises(AssertionError):
        check_archive(archive)


def test_examples_are_source_distribution_only(tmp_path):
    wheel = tmp_path / "test.whl"
    with zipfile.ZipFile(wheel, "w") as output:
        output.writestr("examples/dataset_catalog.py", b"")
    with pytest.raises(AssertionError):
        check_archive(wheel)
    source = tmp_path / "test.tar.gz"
    with tarfile.open(source, "w:gz") as output:
        member = tarfile.TarInfo("meldstore/examples/dataset_catalog.py")
        output.addfile(member, io.BytesIO(b""))
    check_archive(source)


def test_release_selection_rejects_missing_or_mixed_versions_without_deleting(tmp_path):
    version = "0.1.0rc1"
    with pytest.raises(RuntimeError):
        release_artifacts(tmp_path, version)
    wheel = tmp_path / f"meldstore-{version}-py3-none-any.whl"
    source = tmp_path / f"meldstore-{version}.tar.gz"
    wheel.touch()
    with pytest.raises(RuntimeError):
        release_artifacts(tmp_path, version)
    source.touch()
    assert set(release_artifacts(tmp_path, version)) == {wheel, source}
    old = tmp_path / "meldstore-0.0.0.dev0-py3-none-any.whl"
    old.touch()
    with pytest.raises(RuntimeError):
        release_artifacts(tmp_path, version)
    assert old.exists() and wheel.exists() and source.exists()
