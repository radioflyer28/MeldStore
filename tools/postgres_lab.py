"""Run the pinned-provider contract against a disposable loopback PostgreSQL 17.

uv run --frozen --extra test --group postgres-test python tools/postgres_lab.py
No ambient DSN is used. Docker removes only this invocation's generated container.
"""

import argparse
import json
import os
import platform
import secrets
import subprocess
import sys
import time
from contextlib import ExitStack
from datetime import datetime, timezone
from importlib.metadata import distribution, version
from pathlib import Path

import psycopg

IMAGE = "postgres:17@sha256:67f41722b7a8cbdb868a44a4995c846eddfdc2973bccb291ce937dce88ad5675"
ROOT = Path(__file__).resolve().parents[1]


def docker(*args):
    return subprocess.check_output(["docker", *args], text=True).strip()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report-dir", type=Path, default=ROOT / ".qualification/postgres-provider")
    parser.add_argument('--with-rustfs', action='store_true',
                        help='Also qualify PostgreSQL/S3 identity and local coordinator admission')
    args = parser.parse_args()
    started = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    report = args.report_dir.resolve() / (started + "-" + secrets.token_hex(3))
    report.mkdir(parents=True)
    name = "meldstore-pg-provider-" + secrets.token_hex(8)
    password = secrets.token_hex(24)
    environment = {**os.environ, "POSTGRES_PASSWORD": password}
    container_created = False
    services = ExitStack()
    evidence = {
        "started_utc": started, "image": IMAGE, "python": platform.python_version(),
        "platform": platform.system(), "psycopg": version("psycopg"),
        "psycopg_binary": version("psycopg-binary"), "pytest": version("pytest"),
        "provider_source": json.loads(distribution("melddb").read_text("direct_url.json") or "{}"),
        "scope": "provider and SQL catalog parity; payload deployment remains unqualified",
    }
    try:
        # The password is inherited, not included in process arguments or reports.
        subprocess.run([
            "docker", "run", "--detach", "--name", name,
            "--label", "meldstore.qualifier=postgres-provider",
            "--publish", "127.0.0.1::5432", "--env", "POSTGRES_PASSWORD",
            "--env", "POSTGRES_USER=meldstore_lab", "--env", "POSTGRES_DB=meldstore_provider",
            "--mount", "type=tmpfs,destination=/var/lib/postgresql/data", IMAGE,
        ], env=environment, check=True, capture_output=True, text=True)
        container_created = True
        bindings = json.loads(docker("inspect", "--format", "{{json .NetworkSettings.Ports}}", name))
        ports = bindings["5432/tcp"]
        if len(ports) != 1 or ports[0]["HostIp"] != "127.0.0.1":
            raise RuntimeError("Docker did not honor the loopback-only binding")
        dsn = psycopg.conninfo.make_conninfo(
            host="127.0.0.1", port=ports[0]["HostPort"], user="meldstore_lab",
            password=password, dbname="meldstore_provider", connect_timeout=2,
        )
        deadline = time.monotonic() + 45
        while True:
            try:
                with psycopg.connect(dsn, autocommit=True) as conn:
                    evidence["server_version"] = conn.execute("SHOW server_version").fetchone()[0]
                break
            except psycopg.OperationalError:
                if time.monotonic() >= deadline:
                    raise RuntimeError("Disposable PostgreSQL did not become ready") from None
                time.sleep(0.5)
        evidence["image_id"] = docker("inspect", "--format", "{{.Image}}", name)
        evidence['native_tools'] = {tool: docker('exec', name, tool, '--version')
                                    for tool in ('pg_dump', 'pg_restore', 'psql')}
        print("PostgreSQL ready: " + evidence["server_version"], flush=True)
        print("Provider pin: " + evidence["provider_source"].get("vcs_info", {}).get("commit_id", "MISSING"),
              flush=True)
        test_environment = {**os.environ, "MELDSTORE_TEST_POSTGRES": dsn,
                            "MELDSTORE_PG_CONTAINER": name}
        selection = []
        if args.with_rustfs:
            from s3_lab import IMAGE as rustfs_image
            from s3_lab import Lab
            lab = services.enter_context(Lab())
            evidence['rustfs_image'] = rustfs_image
            evidence['scope'] = 'provider, SQL catalog, lifecycle, single-host PostgreSQL/S3, cutover and native recovery'
            test_environment.update(MELDSTORE_S3_CONFIG=json.dumps(lab.config),
                                    MELDSTORE_S3_BUCKET=lab.bucket)
        else:
            selection = ['--ignore=tests/postgres_provider/test_coordination_postgres.py',
                         '--ignore=tests/postgres_provider/test_lifecycle_postgres.py',
                         '--ignore=tests/postgres_provider/test_transfer_postgres.py',
                         '--ignore=tests/postgres_provider/test_archive_postgres.py',
                         '--ignore=tests/postgres_provider/test_deployment_postgres.py']
        result = subprocess.run([
            sys.executable, "-m", "pytest", "tests/postgres_provider", "-q", "--tb=short",
            *selection, "--postgres-provider-required", f"--junitxml={report / 'results.xml'}",
        ], cwd=ROOT, env=test_environment, check=False)
        evidence["pytest_exit_code"] = result.returncode
        return result.returncode
    finally:
        try:
            services.close()
        finally:
            if container_created:
                docker("rm", "--force", "--volumes", name)
                evidence["container_removed"] = True
                print("Removed this run's disposable PostgreSQL container.", flush=True)
            (report / "environment.json").write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
            print(f"Local qualification evidence: {report}", flush=True)



if __name__ == "__main__":
    raise SystemExit(main())
