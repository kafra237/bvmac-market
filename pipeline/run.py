#!/usr/bin/env python3
"""Run the BVMAC public-data pipeline end to end.

This is intentionally small: the downloader and extractor remain independent
modules, while this runner only coordinates them and applies the filesystem
permissions expected by the production services.
"""
from __future__ import annotations

import argparse
import fcntl
import grp
import os
from pathlib import Path
import subprocess
import sys
from datetime import datetime, timezone


def log(message: str) -> None:
    print(f"[{datetime.now(timezone.utc).isoformat()}] {message}", flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="Download BVMAC bulletins and rebuild the master workbook.")
    parser.add_argument("--data-dir", default=os.environ.get("BVMAC_DATA_DIR", "/var/lib/bvmac"))
    parser.add_argument("--start", default=os.environ.get("BVMAC_PIPELINE_START", "2019-01-01"))
    parser.add_argument("--repair-days", type=int, default=int(os.environ.get("BVMAC_REPAIR_DAYS", "120")))
    args = parser.parse_args()

    base = Path(__file__).resolve().parent
    data = Path(args.data_dir)
    bulletins = data / "bulletins"
    output = data / "output"
    report = output / "bvmac_download_report.json"
    master = output / "bvmac_master.xlsx"
    lock_path = Path("/run/lock/bvmac-pipeline.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)

    with lock_path.open("w") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            log("Another BVMAC pipeline run is already active; this run is skipped.")
            return 0

        bulletins.mkdir(parents=True, exist_ok=True)
        output.mkdir(parents=True, exist_ok=True)

        log("Checking and downloading BVMAC bulletins")
        subprocess.run(
            [
                sys.executable,
                str(base / "bvmac_downloader.py"),
                "--start", args.start,
                "--out", str(bulletins),
                "--report", str(report),
                "--repair-days", str(args.repair_days),
                "--publication-grace-days", "10",
                "--skip-sitemap",
                "--skip-probe",
                "--update",
            ],
            check=True,
        )

        log("Extracting the master workbook")
        subprocess.run(
            [
                sys.executable,
                str(base / "bvmac_extract.py"),
                "--pdf-dir", str(bulletins),
                "--out", str(master),
                "--mode", "update",
            ],
            check=True,
        )

        try:
            gid = grp.getgrnam("bvmacdata").gr_gid
            for path in (master, report):
                if path.exists():
                    os.chown(path, -1, gid)
                    path.chmod(0o640)
        except KeyError:
            # Local developer runs do not necessarily have the production group.
            pass

        log("Pipeline completed successfully")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
