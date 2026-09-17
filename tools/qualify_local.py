"""Small reproducible local-file qualification, not the full S08 workload benchmark."""

import argparse
import json
import sys
import tempfile
import time
from pathlib import Path

from meldstore import BlobSchema, Catalog, LocalStorage, Store, Text


def peak_rss():
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        class Counters(ctypes.Structure):
            _fields_ = [("cb", wintypes.DWORD), ("faults", wintypes.DWORD)] + [
                (name, ctypes.c_size_t)
                for name in (
                    "peak",
                    "working",
                    "paged_peak",
                    "paged",
                    "nonpaged_peak",
                    "nonpaged",
                    "pagefile",
                    "pagefile_peak",
                )
            ]

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        psapi = ctypes.WinDLL("psapi", use_last_error=True)
        kernel.GetCurrentProcess.restype = wintypes.HANDLE
        psapi.GetProcessMemoryInfo.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(Counters),
            wintypes.DWORD,
        ]
        counters = Counters()
        counters.cb = ctypes.sizeof(counters)
        if not psapi.GetProcessMemoryInfo(
            kernel.GetCurrentProcess(), ctypes.byref(counters), counters.cb
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        return counters.peak
    import resource

    value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return value if sys.platform == "darwin" else value * 1024


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mib", type=int, default=128)
    parser.add_argument("--adapter", choices=("sqlite", "melddb"), default="melddb")
    args = parser.parse_args()
    if not 1 <= args.mib <= 512:
        parser.error("--mib must be 1..512")
    with tempfile.TemporaryDirectory(prefix="meldstore-qualification-") as directory:
        root = Path(directory)
        source = root / "source.bin"
        block = b"0123456789abcdef" * (1024 * 1024 // 16)
        with source.open("wb") as stream:
            for _ in range(args.mib):
                stream.write(block)
        with Catalog(root / "catalog.db", adapter=args.adapter) as catalog:
            store = Store(catalog, LocalStorage(root / "objects", staging_directory=root))
            schema = BlobSchema("qualification", {"label": Text(required=True)})
            store.install_schema(schema)
            baseline = peak_rss()
            start = time.perf_counter()
            blob = store.import_file(source, schema=schema, metadata={"label": "synthetic"})
            imported = time.perf_counter()
            with store.materialize(blob["id"]) as result:
                assert result.stat().st_size == args.mib * 1024 * 1024
                with result.open("rb") as stream:
                    for _ in range(args.mib):
                        assert stream.read(len(block)) == block
                    assert stream.read(1) == b""
            finished = time.perf_counter()
            exported = store.export_file(blob["id"], root / "exported.bin")
            with exported.open("rb") as stream:
                for _ in range(args.mib):
                    assert stream.read(len(block)) == block
                assert stream.read(1) == b""
            exported_at = time.perf_counter()
            print(
                json.dumps(
                    {
                        "adapter": args.adapter,
                        "size_bytes": blob["byte_size"],
                        "digest": blob["digest"],
                        "import_seconds": imported - start,
                        "materialize_and_compare_seconds": finished - imported,
                        "export_and_compare_seconds": exported_at - finished,
                        "baseline_peak_rss_bytes": baseline,
                        "final_peak_rss_bytes": peak_rss(),
                        "additional_peak_rss_bytes": peak_rss() - baseline,
                    }
                )
            )


if __name__ == "__main__":
    main()
