# Contributor map

If you are changing what users see, start in `web/`. If you are changing an HTTP endpoint, start in `backend/api/`. Scheduled server behaviour belongs in `backend/jobs/` or `operations/`. Source-document parsing belongs in `pipeline/`. SQL contracts belong in `database/`. Model inference belongs in `ml/`.

A good pull request includes a focused test. For data changes, show a small before/after example. For responsive work, test 360, 390, 430, 768, 820, 1024, 1366 and 1920 px. For ML work, keep chronological validation and compare against simple baselines.
