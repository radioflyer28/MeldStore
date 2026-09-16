"""Fresh-process memory/timing evidence against the disposable lab, not AWS."""

import argparse
import json
import os
import tempfile
import time
from pathlib import Path
from uuid import uuid4

from qualify_local import peak_rss

from meldstore import BlobSchema, Catalog, S3Storage, Store


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mib", type=int, choices=(32, 256), required=True)
    args = parser.parse_args()
    config = json.loads(os.environ["MELDSTORE_S3_CONFIG"])
    if not config["endpoint"].startswith("http://127.0.0.1:"):
        raise ValueError("Disposable loopback lab required")
    with tempfile.TemporaryDirectory(prefix="meldstore-s3-memory-") as directory:
        root = Path(directory)
        source = root / "source"
        block = b"0123456789abcdef" * 65536
        with source.open("wb") as output:
            for _ in range(args.mib):
                output.write(block)
        storage = S3Storage(os.environ["MELDSTORE_S3_BUCKET"],
                            prefix="memory/" + uuid4().hex,
                            coordination_directory=root / "coordinator", config=config)
        with Catalog(root / "catalog.db") as catalog:
            store = Store(catalog, storage)
            schema = BlobSchema("dataset", {})
            store.install_schema(schema)
            baseline = peak_rss()
            started = time.perf_counter()
            record = store.import_file(source, schema=schema, metadata={})
            uploaded = time.perf_counter()
            with store.materialize(record["id"]) as path:
                with path.open("rb") as stream:
                    for _ in range(args.mib):
                        assert stream.read(len(block)) == block
                    assert stream.read(1) == b""
            finished = time.perf_counter()
            print(json.dumps({"mib": args.mib, "baseline_peak_rss": baseline,
                              "peak_rss": peak_rss(), "additional_peak_rss": peak_rss() - baseline,
                              "import_seconds": uploaded - started,
                              "materialize_seconds": finished - uploaded}), flush=True)


if __name__ == "__main__":
    main()
