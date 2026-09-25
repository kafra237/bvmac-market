# Data pipeline

The pipeline has three files with distinct jobs.

- `bvmac_downloader.py` finds and downloads public BVMAC bulletins and repairs missing recent dates.
- `bvmac_extract.py` parses those PDFs and produces the canonical `bvmac_master.xlsx`.
- `run.py` coordinates the two modules and applies the production file permissions.

The workbook is a boundary: downstream code should not duplicate extraction corrections. If a parsing rule must change, change it in the extractor, regenerate the workbook and test the resulting data.

On production, the full pipeline has a daily timer. A separate lightweight watcher checks every 30 minutes whether a newer publication appears available and starts the full pipeline only when necessary.
