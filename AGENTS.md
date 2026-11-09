# Project Components and Architecture

This document describes the functional components that comprise the Western Flyer flow-through (SBE 21 thermosalinograph) processing pipeline and provides guidelines for AI agents interacting with this repository.

## Functional Components

The codebase is organized into modular components, each responsible for a distinct stage of the data lifecycle.

### 1. Ingestion Component (`sbe21_ingestion.py`)
**Role:** Processed Data Interface & Quality Flagging
- **Responsibilities:**
    - Reads the `.cnv` files written by SBE Data Processing. Values are used exactly as written; only the file's own `bad_flag` value becomes NULL.
    - Parses the `.cnv` header (start time, NMEA position, station and operator, sensor serial numbers and calibration dates, sensor-configuration fingerprint).
    - Builds UTC timestamps from the header `start_time` plus `timeS`.
    - Derives ship speed from latitude/longitude and time.
    - Sets the `qc_flag` bitmask (spikes, implausible ship speed, a first scan far from the header position, implausible salinity or temperature; bit 4 is unused).
    - Loads per-cruise parameters from `config.toml` (`load_config()`).
- **Key Interfaces:** `ingest_sbe21_cnv()`, `find_cnv_files()`, `parse_cnv_header()`, `apply_cnv_qc_flags()`, `load_config()`
- There is no `.hex` / `.xmlcon` reader. The historical raw-format notes are in `docs/sbe21_hex_format.md`.

### 2. Orchestrator Component (`main.py`)
**Role:** Workflow Manager & Persistence
- **Responsibilities:**
    - Orchestrates the end-to-end pipeline for a specific cruise.
    - Manages CLI arguments (`python main.py <cruise_id> [--db PATH] [--verbose]`), logging, and directory resolution.
    - Ensures atomic and idempotent database commits to DuckDB: per-cruise rows are replaced in a single transaction.
    - Records build provenance (git commit hash, sensor-configuration fingerprint, counts and timestamps) in `build_metadata`, each `.cnv` (name, SHA-256, rows matched, status) in `flowthru_cnv`, and sensor serials and calibration dates in `flowthru_calibration`.
- **Key Interface:** `main()` entry point and `commit_to_duckdb()`.

### 3. Analysis Component (`flowthru_holoviews.py`)
**Role:** Interactive Visualization & Export
- **Responsibilities:**
    - Serves a read-only Panel dashboard with tabs: Track Map (animated), Multi-Axis Profile, File Summary and Data Table.
    - Reads from DuckDB (read-only connection) and updates all tabs when the cruise, file, variable, time zone, time range or QC selection changes.
    - Facilitates CSV export of the selected data.
- **Key Interface:** `dashboard.servable()`

### 4. Infrastructure Component (Scripts)
**Role:** Environment-Specific Automation
- **Linux/macOS (`scripts/linux_mac/`):** Bash scripts for building the database (one cruise, one script per cruise, or all cruises) and launching the dashboard.
- **Windows (`scripts/windows/`):** Batch files for equivalent operations in a CMD environment.

---

## AI Agent Interaction Guidelines

When an AI agent interacts with this repository, it should adhere to the following operational patterns:

### Adding a New Cruise
1. **Directory Structure:** Create `cruises/<cruise_id>/`. The directory name is the cruise ID.
2. **Processed Data:** Place the SBE Data Processing `.cnv` files in `cruises/<cruise_id>/cnv/` (or directly in the cruise folder). A cruise folder with no `.cnv` files stops the build with an error. `.hex` and `.xmlcon` files are ignored.
3. **Configuration:** Optionally add `config.toml` (QC limits, timing). Without it the build logs a warning and uses default limits.
4. **Positions:** There is no `stations.csv`. Position comes from the latitude and longitude columns in each `.cnv`, as signed decimal degrees (the same convention as the CTD and echogram `stations.csv` files).

### Modifying the Pipeline
- **Ingestion or QC Changes:** `sbe21_ingestion.py`. Keep the `qc_flag` bitmask documented in `README.md` in step with the code.
- **Database Schema:** If adding columns, update the table-building and commit logic in `main.py` and the queries in `flowthru_holoviews.py`.
- Do not modify the sibling CTD and echogram projects (`westernflyer_cruise_ctd`, `westernflyer_cruise_echogram`); this repository is independent of them.

### Execution and Verification
- **Run the Build:** Use `python main.py <cruise_id>` to process data.
- **Logs:** Monitor `logs/wf_flowthru_build_<cruise_id>_<timestamp>.log` for detailed processing diagnostics.
- **Database:** Verify output in `processed/wf_flowthru.duckdb`. The `build_metadata` and `flowthru_cnv` tables should be checked to confirm successful persistence and provenance.

### Dependencies
- This project requires **Python 3.13**.
- Other dependencies are listed in `pyproject.toml`.
