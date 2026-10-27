# Western Flyer Flow-Through Data

This repository contains the processing pipeline and visualization suite for flow-through thermosalinograph data collected aboard the R/V Western Flyer.

## Feature Summary

The Western Flyer flow-through codebase is a modular processing pipeline for the ship's Sea-Bird SBE 21 SeaCAT thermosalinograph (S/N 3507). It provides a standardized framework for raw hex data ingestion, position and time reconstruction, quality flagging, and interactive visualization. It mirrors the layout of the sister project [westernflyer_cruise_ctd](../westernflyer_cruise_ctd).

* **Raw .hex Processing:** The pipeline starts from raw Seasave `.hex` files. It parses both 40- and 46-character row formats, skips malformed lines, decodes NMEA latitude/longitude and depth, and builds UTC timestamps.
* **SBE Data Processing .cnv Integration:** The Sea-Bird Scientific Python toolkit does not support the SBE 21, so the pipeline does not convert counts itself. When SBE Data Processing `.cnv` files are present they are matched to the `.hex` scans and add `temp_c`, `cond_sm` and `sal_psu` (plus `fluor_mgm3`, `cstar_atten_m` and `cstar_trans_pct` when those variables are in the `.cnv`). Files without a `.cnv` are loaded as raw counts and listed in the log.
* **Modular Cruise Architecture:** Each cruise is isolated under `cruises/<cruise_id>/`. Adding a new cruise requires only creating a directory with the raw `.hex` files and a `config.toml`; the directory name is the cruise ID.
* **Sidecar Data Integration:** The pipeline joins raw scans with `config.toml` (time source and QC limits), the `.xmlcon` / `.XMLCON` configuration files (calibration, recorded for provenance), and the `.cnv` files (converted values) automatically.
* **Automated QC Flagging:** Scans are flagged, never removed. A `qc_flag` bitmask marks spikes, implausible ship speed, missing NMEA depth, a first scan far from the header position, and implausible converted salinity or temperature (cell not flowing seawater).
* **Atomic, Idempotent Storage:** Processed data is managed via DuckDB. Per-cruise records are replaced atomically inside a single transaction. A `build_metadata` table records build provenance, and a `flowthru_cnv` table records each `.cnv` (name, SHA-256, rows matched, status).
* **Per-Build Audit Logging:** Each invocation writes a fresh, timestamped log file (`logs/wf_flowthru_build_<cruise>_<timestamp>.log`).
* **Interactive Dashboard Suite:** A built-in visualization suite powered by the HoloViz ecosystem (Panel, HoloViews, Bokeh, GeoViews) renders an animated track map with stacked time series, a file summary, and a data table with CSV export. Plot downloads export with an opaque white background.

## Current Status

**Raw counts only unless `.cnv` files are supplied.** The `.hex` headers carry no calibration, so temperature, conductivity and salinity are not computed until the SBE 21 `.xmlcon` / calibration sheet is available or `.cnv` files are imported.

## Root Directory Notes

The project root can be named anything (e.g., `westernflyer_cruise_flowthru`).

To avoid Windows permission issues, place the root folder inside your Windows user directory:
`C:\Users\<your_username>\westernflyer_cruise_flowthru`

## Python Installation Requirements

This project requires **Python 3.12**.

### Windows
When installing, the installer will ask: "Add Python to PATH?" **Select NO.**

* **Typical path:** `C:\Users\<your_username>\AppData\Local\Programs\Python\Python312\`

### macOS (Homebrew)
    brew install python@3.12

### Linux (Ubuntu / Debian) using apt install
    sudo add-apt-repository ppa:deadsnakes/ppa
    sudo apt update
    sudo apt install python3.12 python3.12-venv

### Linux (Ubuntu / Debian) using pyenv

This has the advantage of not requiring root privileges, and it does not install Python 3.12 system wide.

    # Install pyenv:
    curl -fsSL https://pyenv.run | bash
    # Install Python 3.12:
    pyenv install 3.12
    # Set Python 3.12 as the global default:
    pyenv global 3.12

## Project Structure

*`logs/` and `processed/` are created automatically on first run.*

    .
    |   flowthru_holoviews.py
    |   main.py
    |   sbe21_ingestion.py
    |   pyproject.toml
    +---cruises
    |   +---2025_Baja
    |   |   |   config.toml                                       time source and QC limits
    |   |   |
    |   |   +---cnv
    |   |   |       20250415.cnv      SBE Data Processing output (same name as the .hex)
    |   |   |       ... (etc)
    |   |   |
    |   |   +---master_xmlcon         the two original configurations the per-file copies were made from
    |   |   |       flowthrough_master_config-20241112.xmlcon             (NMEA depth off)
    |   |   |       flowthrough_master_config-20241112_nmea_depth_on.xmlcon
    |   |   |
    |   |   \---hex
    |   |           20250415.hex
    |   |           20250415.XMLCON   one .XMLCON beside every .hex (same name)
    |   |           20250416.hex
    |   |           20250416.XMLCON
    |   |           ... (etc)
    |   |
    |   +---20250611_Science_charter_with_Kerckhoff_Marine_Lab
    |   |   |   config.toml
    |   |   |
    |   |   +---cnv
    |   |   |       20250611.cnv
    |   |   |
    |   |   \---hex
    |   |           20250611.hex
    |   |           20250611.XMLCON
    |   |
    |   \---... (one folder per cruise, same layout)
    +---logs
    +---processed
    \---scripts
        +---linux_mac
        |       flowthru_build.sh                  (generic; takes <cruise_id>)
        |       flowthru_build_all.sh              (builds every cruise under cruises/)
        |       flowthru_build_2025_Baja.sh        (wrapper around flowthru_build.sh 2025_Baja)
        |       flowthru_build_2025_Baja_cnv.sh    (same build, uses the .cnv files)
        |       flowthru_build_<cruise_id>.sh      (one wrapper per cruise)
        |       flowthru_dashboard.sh
        |
        \---windows
                flowthru_build.bat                 (generic; takes <cruise_id>)
                flowthru_build_all.bat             (builds every cruise under cruises\)
                flowthru_build_2025_Baja.bat       (wrapper around flowthru_build.bat 2025_Baja)
                flowthru_build_2025_Baja_cnv.bat   (same build, uses the .cnv files)
                flowthru_build_<cruise_id>.bat     (one wrapper per cruise)
                flowthru_dashboard.bat

## Cruises

| Cruise ID | Dates | Hex files | .cnv files | Notes |
|---|---|---|---|---|
| `2025_Baja` | Apr 15 - May 26, 2025 | 28 | 28 | Sessions saved no `.XMLCON` of their own, so each `.hex` has a copy of one of the two original configurations (kept in `master_xmlcon/`): NMEA depth off for 6 files, on for 22, chosen by each file's scan-line length. These copies are derived, not Seasave originals. Two files (`20250506`, `20250509`) each have 7 truncated lines, which are skipped; they still count toward the scan index so later scan times stay correct. |
| `20250611_Science_charter_with_Kerckhoff_Marine_Lab` | Jun 11, 2025 | 1 | 1 | |
| `20250724_Education_program_cruise_with_Peer_Mentor_Program` | Jul 24, 2025 | 1 | 1 | |
| `20250815_Test_run_for_the_pH_probe` | Aug 15, 2025 | 1 | 1 | |
| `20250818_Testing_new_pH_probe` | Aug 18, 2025 | 1 | 1 | |
| `20250916_Cabrillo_College_Day_1` | Sep 16, 2025 | 1 | 1 | |
| `20250923_CSUMB_Invert_Zoo_Class_Cruise` | Sep 23, 2025 | 1 | 1 | |
| `20250925_Cabrillo_College_Field_Studies_Course_Day_2` | Sep 25, 2025 | 1 | 1 | |
| `20251021_Class_cruise_for_MPC_marine_bio_course` | Oct 21, 2025 | 2 | 2 | |
| `20251028_MPC_intro_marine_bio_class_day_2` | Oct 28, 2025 | 1 | 1 | |

## File Descriptions

* **main.py** — Orchestrator. Parses CLI args, loads the `config.toml` and calibration files, runs the per-file pipeline, matches `.cnv` files, writes the DuckDB atomically, and stamps build provenance.
* **sbe21_ingestion.py** — SBE 21 hex reader. Parses headers and scan rows, decodes NMEA position and depth, builds timestamps, sets QC flags, matches and merges `.cnv` files, and provides the calibration hook.
* **flowthru_holoviews.py** — Read-only Panel dashboard. Reactive widgets update all tabs when the cruise or file selection changes.
* **pyproject.toml** — Package definition. Enables `pip install -e .` to install the project and all dependencies in one step.
* **config.toml** — Processing constants per cruise (time source, QC limits). See schema below.
* **.xmlcon / .XMLCON** — SBE 21 configuration file (calibration coefficients). Required by SBE Data Processing to create the `.cnv` files; `main.py` reads and records it but does not use it to convert counts. Every cruise follows one rule: a `.XMLCON` with the same name sits beside each `.hex` in `hex/`. It takes priority; a single cruise-level `.xmlcon` is only a fallback (`--xmlcon` overrides both).
* **.cnv** — SBE Data Processing output. Supplies converted temperature, conductivity and salinity.

## Prerequisites

**Git** must be installed to download the software from GitHub (`git clone`). Download it from [git-scm.com](https://git-scm.com/downloads) and confirm with `git --version`.

Python 3.12 is required (see above). The dashboard and pipeline dependencies are declared in `pyproject.toml` and installed from PyPI when you run `pip install -e .`. The SBE 21 is not supported by the Sea-Bird Scientific Python toolkit, so that toolkit is not used here.

## Getting Started

    git clone https://github.com/joeacarlisle/westernflyer_cruise_flowthru.git
    cd westernflyer_cruise_flowthru

Then follow the **Prerequisites** and **Setup** sections to install Python 3.12 and the project dependencies.

## Setup: Virtual Environment and Installation

*Run from the project root directory.*

### Windows (Command Prompt)

    "C:\Users\<your_username>\AppData\Local\Programs\Python\Python312\python.exe" -m venv .venv
    .venv\Scripts\activate
    python -m pip install -e .

### macOS / Linux

    python3.12 -m venv .venv
    source .venv/bin/activate
    python -m pip install -e .

`pip install -e .` reads `pyproject.toml` and installs all dependencies in one step, pinned to the tested versions.

The `-e` (editable) flag means source changes are picked up immediately without reinstalling.

## Preparing a New Cruise

| Step | File(s) | Location | Needed? |
|---|---|---|---|
| 1 | Cruise directory | `cruises/<cruise_id>/` | **Required** |
| 2 | Raw `.hex` files | `cruises/<cruise_id>/hex/` | **Required** |
| 3 | `config.toml` (copy from an existing cruise, e.g. `2025_Baja`; see schema below) | `cruises/<cruise_id>/config.toml` | **Required** |
| 4 | SBE 21 `.XMLCON` beside each `.hex` (same name) | `cruises/<cruise_id>/hex/` | **Required to run SBE Data Processing** (it is needed to create the `.cnv` files); not needed by `main.py` itself |
| 5 | SBE Data Processing `.cnv` files | `cruises/<cruise_id>/cnv/` | **Required for temperature, conductivity and salinity** |

* **Why step 4 matters:** SBE Data Processing needs the instrument's `.xmlcon` configuration file (the calibration coefficients) to convert the raw `.hex` counts into temperature, conductivity and salinity and write the `.cnv` files. Without the `.xmlcon` you cannot produce the `.cnv` files. Keep the `.XMLCON` beside its `.hex` as the record of the calibration used. `main.py` itself only reads and records it (file name, SHA-256, sensor serial numbers and calibration dates). It does not convert counts.
* **Why step 5 matters:** the pipeline does not convert counts. Temperature, conductivity and salinity (plus fluorescence and beam transmission when present) come only from the `.cnv` files. The build still runs without them, but every file without a `.cnv` is loaded as raw counts only, and the maps and charts then show counts instead of temperature and salinity. Use `--require-cnv` to stop the build if any `.hex` has no `.cnv`.

### .cnv matching

* Place each `.cnv` in `cruises/<cruise_id>/cnv/` (or the cruise folder) with the `.hex` name, e.g. `20250611.cnv` (a `_suffix` is allowed). Use the final `.cnv` with salinity.
* Matching is by the `.cnv` `scan` column, and checked against the `.hex` latitude/longitude. A `.cnv` whose position does not agree is rejected, not merged.
* A `.cnv` with fewer rows than the `.hex` has scans is applied to those scans only and logged as a warning (re-run Data Conversion with all scans).
* Files without a `.cnv` are loaded as raw counts (listed in the log). `--require-cnv` stops the build instead.

## Running the Pipeline

**Activate the virtual environment first.** Required every time you open a new terminal.

### Windows (Command Prompt)
    cd C:\Users\<your_username>\westernflyer_cruise_flowthru
    .venv\Scripts\activate

You should see `(.venv)` at the start of your prompt.

### macOS / Linux
    cd /path/to/westernflyer_cruise_flowthru
    source .venv/bin/activate

Make the scripts executable once (first time only):

    chmod +x scripts/linux_mac/*.sh

---

Run the build with the cruise ID as the argument:

* **Windows:** `scripts\windows\flowthru_build.bat <cruise_id>`
* **Linux / macOS:** `./scripts/linux_mac/flowthru_build.sh <cruise_id>`

Each cruise also has a shortcut script with the cruise ID filled in, for example `scripts\windows\flowthru_build_2025_Baja.bat`. `flowthru_build_2025_Baja_cnv.bat` runs the same build and is the Baja 2025 script that documents the use of the `.cnv` files. To rebuild every cruise under `cruises/` one after another, run:

* **Windows:** `scripts\windows\flowthru_build_all.bat`
* **Linux / macOS:** `./scripts/linux_mac/flowthru_build_all.sh`

Extra arguments (such as `--require-cnv`) are passed on to `main.py`. A cruise's rows in the database are replaced each time it is built, so cruises renamed after an earlier build leave their old rows behind. To remove those, delete `processed/wf_flowthru.duckdb` and rebuild.

Re-run `main.py` after any change to `sbe21_ingestion.py` or `config.toml`; the dashboard only reads the existing database.

Alternatively, call `main.py` directly for full control. The following are example invocations — run only the one that fits your needs:

    # Basic build
    python main.py 2025_Baja

    # Stop unless every .hex file has a matching .cnv
    python main.py 2025_Baja --require-cnv

    # Write output to a custom DuckDB path
    python main.py 2025_Baja --db /path/to/custom.duckdb

    # Override the xmlcon or hex directory locations
    python main.py 2025_Baja --xmlcon /path/to/custom.xmlcon
    python main.py 2025_Baja --hex-dir /path/to/hex_files/

### CLI arguments

| Argument           | Default                                  | Description                                          |
|--------------------|------------------------------------------|------------------------------------------------------|
| `cruise_id`        | *(required)*                             | Cruise directory name under `cruises/`               |
| `--db`             | `processed/wf_flowthru.duckdb`           | DuckDB output path                                   |
| `--xmlcon`         | `.XMLCON` beside each hex (fallback: one in the cruise folder) | Path to SBE 21 configuration file (optional)    |
| `--hex-dir`        | `cruises/<id>/hex/`                      | Directory containing raw `.hex` files                |
| `--require-cnv`    | Off                                      | Stop unless every `.hex` file has a matching `.cnv`  |
| `--verbose` / `-v` | Off                                      | Enable DEBUG-level logging                           |

### Output tables

`flowthru_data` has one row per scan: `cruise_id, file_key, scan_index, time_utc, lat, lon, depth_m, ch0_counts ... ch5_raw, aux_byte, flag_byte, speed_ms, qc_flag, station_name, operator, sample_interval_s`, plus the converted columns (`temp_c`, `cond_sm`, `sal_psu` and others) when a `.cnv` is matched. `build_metadata` records build provenance, and `flowthru_cnv` records each matched `.cnv` (name, SHA-256, rows matched, status).

## Running the Dashboard

* **Windows:** `scripts\windows\flowthru_dashboard.bat`
* **Linux / macOS:** `./scripts/linux_mac/flowthru_dashboard.sh`

The flow-through dashboard starts on port 5007 at `http://localhost:5007`. The CTD dashboard in [westernflyer_cruise_ctd](../westernflyer_cruise_ctd) starts on port 5006, so the two applications can run side by side.

Or directly: `panel serve flowthru_holoviews.py --port 5007 --show`. The dashboard lists `2025_Baja` first and opens on it; set `WF_DEFAULT_CRUISE` to open on a different cruise.

Temperature, conductivity, salinity and fluorescence (from the imported `.cnv` files) appear in "Colour map by", as time-series panels (flagged scans marked in black), and as median columns on the File Summary tab. A sidebar warning appears for cruises with no converted values. Variables are raw counts until the `temp_c` / `cond_sm` / `sal_psu` columns exist, then they appear automatically.

Tabs: Track Map (map on the left, time series on the right), Multi-Axis Profile (temperature, salinity and fluorescence on one time axis, each with its own colour-matched y-axis; switch between clock time and elapsed minutes; click a legend entry to mute a trace), File Summary, Data Table (CSV export). Beam transmission is not plotted because it is not in the database.

**Track Map** has an animated cruise timeline (checkbox "Animate timeline"; uncheck for the static coloured track):

* Play / pause / step / scrub with the player; Speed sets frames per second.
* Minutes per frame sets how much cruise time one frame covers (5, 10, 30, 60).
* Trail: whole cruise so far, or only the last 6 h / 24 h / 3 days. A faint grey line shows the full track.
* Skip gaps (default on): playback jumps to the next frame that has data, so the idle periods between logging sessions are skipped. Turn it off to play in true cruise time; empty frames then show "no data in this interval".
* A red marker shows the ship; the readout above the map gives local time, position, speed and the colour variable.
* The dashboard shows local ship time (Pacific, `America/Los_Angeles`) on the time slider, time axes, animation readout, File Summary and Data Table. The database stays in UTC, and the CSV export carries both `time_utc` and `time_local` columns. Set `WF_DISPLAY_TZ=UTC` to show UTC instead.
* The time-series panels beside the map show the full cruise, and a red cursor line follows the same timeline.
* With animation off, a red highlight shows the window zoomed on the time-series panels.

With 10-minute frames the 2025_Baja cruise is 534 frames (skip gaps) or 5,848 frames (true time).

Plot downloads (the Bokeh toolbar Save button) export with an opaque white background rather than a transparent one.

## config.toml Schema

`config.toml` uses TOML format with named sections (`[metadata]`, `[timing]`, `[qc]`). The cruise ID is **not** stored here — it comes from the directory name.

### Metadata

| Parameter    | Description                                                     |
|--------------|-----------------------------------------------------------------|
| `TSG_SERIAL` | Instrument serial number (informational; not read by pipeline)  |
| `INSTRUMENT` | Instrument description (informational; not read by pipeline)    |

### Timing

| Parameter           | Description                                                                                                                                                                                                       |
|---------------------|-------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `SAMPLE_INTERVAL_S` | Seconds between scans. Leave empty to use "Real-Time Sample Interval" from each header (3 s).                                                                                                                     |
| `TIME_SOURCE`       | `nmea` = header `NMEA UTC (Time)` (GPS); `system` = header `System UTC` (PC clock). Default `nmea`: the PC clock runs about 7.5 min ahead of NMEA in every file. Scan time = start time + scan_index x sample interval. |

### QC Thresholds

| Parameter          | Description                                                                                                                                                                                  |
|--------------------|----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `MAX_SPEED_MS`     | Implied ship speed (m/s) above which a scan is flagged (`qc_flag` bit 2). Default 10.0.                                                                                                       |
| `SPEED_LAG_SCANS`  | Ship speed is measured between positions this many scans apart (default 10), because Seasave latches the last NMEA fix onto each scan and adjacent scans often repeat a position.            |
| `SPIKE_WINDOW`     | Running-median window (scans) for the spike test. Default 9.                                                                                                                                   |
| `SPIKE_COUNTS`     | Raw-count spike limit on ch0/ch1 (`qc_flag` bit 1). Provisional; used for scans without a `.cnv`. Default 300.                                                                                |
| `POSITION_TOL_DEG` | The first scan must be within this many degrees of the header NMEA position (`qc_flag` bit 8). Default 1.0.                                                                                   |
| `SPIKE_TEMP_C`     | Converted-value spike test: temperature differing from its running median by more than this sets `qc_flag` bit 1 (replaces the count-based test for scans with a `.cnv`). Default 0.2 C.     |
| `SPIKE_SAL_PSU`    | Same test for salinity. Default 0.5 PSU.                                                                                                                                                       |
| `MIN_SALINITY_PSU` | Converted salinity below this sets `qc_flag` bit 16 (cell likely not flowing seawater). Default 20.                                                                                           |
| `MIN_TEMP_C` / `MAX_TEMP_C` | Converted temperature outside this range sets `qc_flag` bit 16. Default -2 / 40 C.                                                                                                      |

### qc_flag bitmask

| Bit | Meaning                                                                                      |
|-----|----------------------------------------------------------------------------------------------|
| 1   | Spike (converted temperature / salinity jump from the running median, or raw-count test)     |
| 2   | Implausible ship speed                                                                       |
| 4   | No NMEA depth (informational)                                                                |
| 8   | First scan far from header position                                                          |
| 16  | Converted salinity or temperature out of range, usually the cell not flowing seawater        |

Scans are flagged, never removed.

## Contact

For questions regarding this data archive or the Western Flyer Foundation, please contact **joeacarlisle@gmail.com**
