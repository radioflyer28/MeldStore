"""Install each built artifact in a fresh environment, outside the source checkout."""

import subprocess
import tempfile
from pathlib import Path


def main():
    root = Path(__file__).resolve().parents[1]
    artifacts = list((root / "dist").glob("*.whl")) + list((root / "dist").glob("*.tar.gz"))
    if len(artifacts) != 2:
        raise RuntimeError("Expected exactly one wheel and one sdist in dist/")
    code = """
from meldstore import BlobSchema, Catalog, Text
for adapter in ('sqlite', 'melddb'):
    with Catalog(adapter + '.db', adapter=adapter) as catalog:
        schema = BlobSchema('smoke', {'title': Text(required=True)})
        assert catalog.install_schema(schema) == schema.table_name
        assert catalog.install_schema(schema) == schema.table_name
print('Both adapters passed from installed artifact')
"""
    for artifact in artifacts:
        with tempfile.TemporaryDirectory(prefix="meldstore-package-") as directory:
            subprocess.run(
                [
                    "uv",
                    "run",
                    "--no-project",
                    "--isolated",
                    "--with",
                    str(artifact),
                    "python",
                    "-I",
                    "-c",
                    code,
                ],
                cwd=directory,
                check=True,
            )
        print(f"Verified {artifact.name}", flush=True)


if __name__ == "__main__":
    main()
