# Application architecture

JAGUAR uses Python and PySide6 for the Windows desktop interface and SQLite for authoritative local project storage. The approved MVP covers extraction, validation, review, and Excel reporting. Species classification and ecological analysis remain outside scope.

The application package separates `ui`, `core`, `db`, `ingest`, `adapters`, `ocr`, `workers`, `export`, and `assets`. Later gates will introduce ExifTool, PaddleOCR/OpenCV, MegaDetector V6, isolated inference processes, and openpyxl. Their versions and redistribution conditions require review before installation or bundling.

Migration 1 stores one project per database and its source roots. Foreign keys are enabled on every application connection. Each migration batch runs in a transaction; failure rolls back its DDL and version records. Unknown migration versions are rejected. Exclusive file creation prevents overwrite; opening a missing path does not create a database. A JAGUAR application identifier protects unrelated databases. Backup before future upgrades remains the recovery policy; no destructive downgrade is provided.

Gate 1 has no concurrent workers, so SQLite uses its default journal. A controlled writer and WAL evaluation belong to worker integration. Processing tables will arrive with the stages that use them rather than freezing the handover's proposed schema prematurely.

Gate 2 adds images, image_paths, import_runs, and import_items in migration 2. A single spawned import process holds an OS file lock for its project. SQLite transactions commit each file's identity, path, and successful checkpoint together. The GUI receives bounded progress messages and reads summary results; concurrent imports for one database are rejected. SQLite's default journal remains sufficient for this single-writer import stage.

Discovery streams through nested folders, without following symbolic links or directory junctions. JPEG/PNG content is decoded before acceptance, and hashing uses one-megabyte blocks. SHA-256 identity is unique per project; every matching source path remains stored. Changed content at an existing path creates a new identity while preserving the old path record as unavailable. Missing paths are marked unavailable only after a clean scan.

Interrupted imports resume the latest running or paused run with exactly the same selected folders. Committed files are reused when size and modification time match; changed files are hashed again. A new completed-run import always hashes its inputs again. Changes that deliberately preserve both size and timestamp during a pause cannot be detected by the checkpoint shortcut; a fresh run provides full revalidation. Scan errors and file failures remain visible and durable. Counts describe paths and distinct images within the selected run, including duplicate paths beyond the first matching hash in that run.

The desktop preserves a left sidebar and project context. The supplied dashboard reference informs muted blue-gray surfaces, teal accents, compact KPI tiles, and live SQLite image/import/EXIF coverage panels. Remaining project/camera/date/run filters, review/detection panels, and Excel export depend on later gates. Every metric derives from stored data; the design contract appears in dashboard-design.md.

Gate 3 uses an external ExifTool executable through a fixed read-only argument list, with shell execution disabled, user configuration loading disabled, and a subprocess timeout. Migration 3 introduces exif_runs and exif_records. Each record retains complete raw JSON, stderr, normalized candidates, warnings, source path, extraction time, and run identity. The run stores tool and normalizer versions. Unknown tags remain in raw output. Invalid JSON output is retained with its failure rather than discarded.

Normalized candidates reference their source tags. DateTimeOriginal takes precedence over CreateDate fallback; filesystem dates never become capture dates. Explicit offsets are preserved; missing timezones remain unspecified. Standard EXIF AmbientTemperature is Celsius; other temperature tags require explicit units for conversion. Physical serial candidates remain separate from overlay site labels. Later reconciliation will compare observations rather than overwrite them.

EXIF extraction shares the single-writer project lock with import and runs in a spawned worker. Source hashes are verified against imported identity before and after extraction. A paused or interrupted run resumes outstanding images when tool and normalizer versions match; committed records remain immutable. A fresh run after completion retains prior history and retries all images, including failures.

Configuration currently consists of the project settings JSON and bounded local application logs. Logs contain operational status rather than image content. Model paths, thresholds, reconciliation tolerances, and workbook columns remain later-gate decisions.

Development targets Python 3.13 with pinned PySide6 and build dependencies. The existing MIT license remains unchanged; the handover's contrary license statement requires resolution before release. Third-party binaries and model assets require separate license review.

generated by Alapaap.net - open mind open skies
