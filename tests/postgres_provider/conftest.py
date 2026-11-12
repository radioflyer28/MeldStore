"""Opt-in tests of the installed, Git-pinned provider against a disposable server."""

import json
import os
import re
import tomllib
import uuid
from contextlib import ExitStack
from importlib.metadata import distribution
from pathlib import Path
from types import SimpleNamespace

import melddb
import pytest


class PrivateNamespace(SimpleNamespace):
    def __repr__(self):
        return "<disposable PostgreSQL fixture; connection details redacted>"


def pytest_addoption(parser):
    parser.addoption("--postgres-provider-required", action="store_true", default=False)


def pytest_sessionfinish(session, exitstatus):
    if session.config.getoption("--postgres-provider-required"):
        reporter = session.config.pluginmanager.getplugin("terminalreporter")
        if session.testscollected == 0 or (reporter and reporter.stats.get("skipped")):
            session.exitstatus = pytest.ExitCode.TESTS_FAILED


@pytest.fixture(scope="session")
def provider_environment(request):
    dsn = os.environ.get("MELDSTORE_TEST_POSTGRES")
    if not dsn:
        if request.config.getoption("--postgres-provider-required"):
            pytest.fail("Required PostgreSQL provider fixture has no disposable database")
        pytest.skip("Run tools/postgres_lab.py for disposable PostgreSQL qualification")
    # Missing Psycopg or an unreachable server is a failure once explicitly opted in.
    import psycopg

    project = Path(__file__).resolve().parents[2]
    config = tomllib.loads((project / "pyproject.toml").read_text(encoding="utf-8"))
    dependency = next(d for d in config["project"]["dependencies"] if d.startswith("melddb "))
    pin = dependency.rsplit("@", 1)[1]
    assert re.fullmatch(r"[0-9a-f]{40}", pin), "Provider must have an exact Git pin"
    package = distribution("melddb")
    source = json.loads(package.read_text("direct_url.json") or "{}")
    assert source.get("vcs_info", {}).get("commit_id") == pin, "Installed provider differs from pin"
    assert source.get("url", "").removesuffix(".git") == "https://github.com/radioflyer28/melddb"
    assert Path(melddb.__file__).resolve().is_relative_to(Path(package.locate_file("")).resolve()), (
        "Imported provider is shadowed by a checkout"
    )
    with psycopg.connect(dsn, autocommit=True) as admin:
        version = int(admin.execute("SHOW server_version_num").fetchone()[0])
        assert version // 10000 == 17, "This fixture qualifies PostgreSQL 17 only"
    return PrivateNamespace(dsn=dsn, driver=psycopg, pin=pin)


@pytest.fixture
def pg(provider_environment):
    driver = provider_environment.driver
    namespace = "ms_provider_" + uuid.uuid4().hex
    with driver.connect(provider_environment.dsn, autocommit=True) as admin, ExitStack() as stack:
        admin.execute(driver.sql.SQL("CREATE SCHEMA {}").format(driver.sql.Identifier(namespace)))
        dsn = driver.conninfo.make_conninfo(
            provider_environment.dsn,
            options=f"-c search_path={namespace} -c statement_timeout=5000 -c deadlock_timeout=100",
        )

        def connect(**kwargs):
            return stack.enter_context(melddb.connect(dsn, **kwargs))

        try:
            yield PrivateNamespace(dsn=dsn, admin=admin, connect=connect, driver=driver)
        finally:
            # Close provider connections before dropping only this test's generated schema.
            stack.close()
            admin.execute(
                driver.sql.SQL("DROP SCHEMA {} CASCADE").format(driver.sql.Identifier(namespace))
            )
