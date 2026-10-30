# Western Flyer Flow-Through Data

This repository contains the processing pipeline and visualization suite for flow-through thermosalinograph data collected aboard the R/V Western Flyer.

## Feature Summary

The Western Flyer flow-through codebase is a modular processing pipeline for the ship's Sea-Bird SBE 21 SeaCAT thermosalinograph (S/N 3507). It provides a standardized framework for SBE Data Processing `.cnv` ingestion, ship-speed derivation, quality flagging, and interactive visualization. It mirrors the layout of the sister project [westernflyer_cruise_ctd](https://github.com/joeacarlisle/westernflyer_cruise_ctd).

* **SBE Data Processing .cnv ingestion:** The pipeline reads only the `.cnv` files written by SBE Data Processing. Scans, UTC time (header `start_time` + `timeS`), latitude/longitude, temperature, conductivity, salinity, fluorescence and sound velocity are used exactly as written; the only change is that the file's own `bad_flag` value becomes NULL. `cond_sm`, `temp_c`, `sal_psu` and `fluor_mgm3` (plus `cstar_atten_m` and `cstar_trans_pct` when present) are stored under those names.
* **No .hex or .xmlcon needed:** `.hex` and `.xmlcon` files in a cruise folder are ignored. The build runs when they are absent. Sensor serial numbers and calibration dates, and a SHA-256 fingerprint of the sensor-configuration XML embedded in each `.cnv` header, are recorded instead.
* **Modular Cruise Architecture:** Each cruise is isolated under `cruises/<cruise_id>/`. Adding a new cruise requires only a directory with the `.cnv` files (in `cnv/` or the cruise folder) and, optionally, a `config.toml`; the directory name is the cruise ID.
* **Derived values:** Ship speed (`speed_ms`) is computed from the `.cnv` latitude/longitude and time. A `.cnv` carries no depth, so `depth_m` is empty and the dashboard hides it.
* **Automated QC Flagging:** Scans are flagged, never removed. A `qc_flag` bitmask marks spikes, implausible ship speed, missing NMEA depth, a first scan far from the header position, and implausible converted salinity or temperature (cell not flowing seawater).
* **Atomic, Idempotent Storage:** Processed data is managed via DuckDB. Per-cruise records are replaced atomically inside a single transaction. A `build_metadata` table records build provenance, and a `flowthru_cnv` table records each `.cnv` (name, SHA-256, rows matched, status).
* **Per-Build Audit Logging:** Each invocation writes a fresh, timestamped log file (`logs/wf_flowthru_build_<cruise>_<timestamp>.log`).
* **Interactive Dashboard Suite:** A built-in visualization suite powered by the HoloViz ecosystem (Panel, HoloViews, Bokeh, GeoViews) renders an animated track map with stacked time series, a file summary, and a data table with CSV export. Plot downloads export with an opaque white background.

## Current Status

**The pipeline runs from `.cnv` files only.** It does not convert counts and does not read `.hex` or `.xmlcon` files. A cruise folder with no `.cnv` files stops the build with an error.

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
    |   |   |       20250415.cnv      SBE Data Processing output (the only data source)
    |   |   |       ... (etc)
    |   |   |
    |   |   +---master_xmlcon         (archive only; not read by the pipeline)
    |   |   |       flowthrough_master_config-20241112.xmlcon             (NMEA depth off)
    |   |   |       flowthrough_master_config-20241112_nmea_depth_on.xmlcon
    |   |   |
    |   |   \---hex                   (archive only; not read by the pipeline)
    |   |           20250415.hex
    |   |           20250415.XMLCON
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
    |   |   \---hex                   (optional; not read)
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
        |       flowthru_build_<cruise_id>.sh      (one wrapper per cruise)
        |       flowthru_dashboard.sh
        |
        \---windows
                flowthru_build.bat                 (generic; takes <cruise_id>)
                flowthru_build_all.bat             (builds every cruise under cruises\)
                flowthru_build_2025_Baja.bat       (wrapper around flowthru_build.bat 2025_Baja)
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

* **main.py** — Orchestrator. Parses CLI args, loads the optional `config.toml`, finds the `.cnv` files, runs the per-file pipeline, writes the DuckDB atomically, and stamps build provenance.
* **sbe21_ingestion.py** — `.cnv` reader (`ingest_sbe21_cnv`). Parses the `.cnv` header and data rows, builds timestamps, derives ship speed, and sets QC flags. The older `.hex` / `.xmlcon` reader functions remain in the module for reference but are not called.
* **flowthru_holoviews.py** — Read-only Panel dashboard. Reactive widgets update all tabs when the cruise or file selection changes.
* **pyproject.toml** — Package definition. Enables `pip install -e .` to install the project and all dependencies in one step.
* **config.toml** — Processing constants per cruise (time source, QC limits). See schema below.
* **.cnv** — SBE Data Processing output and the only data source. Supplies time, position and the converted variables.
* **.hex / .xmlcon** — Not read by the pipeline. They are only needed upstream, by SBE Data Processing, to create the `.cnv` files.

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
| 2 | SBE Data Processing `.cnv` files | `cruises/<cruise_id>/cnv/` (or the cruise folder) | **Required** |
| 3 | `config.toml` (copy from an existing cruise, e.g. `2025_Baja`; see schema below) | `cruises/<cruise_id>/config.toml` | Optional (default QC limits are used if missing) |

* **`.hex` and `.xmlcon` files are not needed.** The pipeline never reads them. Keep them wherever you archive the raw instrument data.
* **Use the final `.cnv`** (the one with salinity). Values are used exactly as written; the pipeline never edits a `.cnv`.

### .cnv requirements

* Any file name is accepted. The file name without `.cnv` becomes the leg name (`file_key`) shown in the dashboard.
* Each `.cnv` needs the `timeS`, `latitude` and `longitude` columns and a `start_time` line in its header. A file that lacks them fails and is listed in the log; the other files still load.
* If the number of data rows differs from the header's `nvalues`, or rows are malformed, the file is loaded and the `flowthru_cnv` status says so.

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

Each cruise also has a shortcut script with the cruise ID filled in, for example `scripts\windows\flowthru_build_2025_Baja.bat`. To rebuild every cruise under `cruises/` one after another, run:

* **Windows:** `scripts\windows\flowthru_build_all.bat`
* **Linux / macOS:** `./scripts/linux_mac/flowthru_build_all.sh`

Extra arguments (such as `--verbose`) are passed on to `main.py`. A cruise's rows in the database are replaced each time it is built, so cruises renamed after an earlier build leave their old rows behind. To remove those, delete `processed/wf_flowthru.duckdb` and rebuild.

Re-run `main.py` after any change to `sbe21_ingestion.py` or `config.toml`; the dashboard only reads the existing database.

Alternatively, call `main.py` directly for full control. The following are example invocations — run only the one that fits your needs:

    # Basic build
    python main.py 2025_Baja

    # Write output to a custom DuckDB path
    python main.py 2025_Baja --db /path/to/custom.duckdb

### CLI arguments

| Argument           | Default                                  | Description                                          |
|--------------------|------------------------------------------|------------------------------------------------------|
| `cruise_id`        | *(required)*                             | Cruise directory name under `cruises/`               |
| `--db`             | `processed/wf_flowthru.duckdb`           | DuckDB output path                                   |
| `--verbose` / `-v` | Off                                      | Enable DEBUG-level logging                           |

### Output tables

`flowthru_data` has one row per scan: `cruise_id, file_key, scan_index, time_utc, lat, lon, depth_m (empty), temp_c, cond_sm, sal_psu, fluor_mgm3, sound_velocity_fts` (plus `cstar_atten_m` and `cstar_trans_pct` when present), `speed_ms, qc_flag, station_name, operator, sample_interval_s`. `build_metadata` records build provenance. `flowthru_cnv` records each `.cnv` (name, SHA-256, rows declared and loaded, status). `flowthru_calibration` records the sensor serials and calibration dates from each `.cnv` header, with a SHA-256 of the embedded sensor-configuration XML (not of the original `.xmlcon`).

## Running the Dashboard

* **Windows:** `scripts\windows\flowthru_dashboard.bat`
* **Linux / macOS:** `./scripts/linux_mac/flowthru_dashboard.sh`

The flow-through dashboard starts on port 5007 at `http://localhost:5007`. The CTD dashboard in [westernflyer_cruise_ctd](https://github.com/joeacarlisle/westernflyer_cruise_ctd) starts on port 5006, so the two applications can run side by side.

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
