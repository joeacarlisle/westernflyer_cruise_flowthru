"""Western Flyer flow-through (SBE 21) build orchestrator.

Usage:
    python main.py <cruise_id> [--xmlcon PATH] [--hex-dir PATH] [--db PATH] [--verbose]

The cruise_id maps to ``cruises/<cruise_id>/`` (same convention as the
westernflyer_cruise_ctd pipeline).  Expected layout:

    cruises/<cruise_id>/
        config.toml           processing parameters (time source, QC limits)
        <any name>.xmlcon     cruise-wide fallback configuration (optional; one file only)
        hex/                  raw Seasave .hex files -- one per logging session,
                              each with its own same-named .XMLCON beside it

Per file:
    parse_hex_header          instrument, NMEA start time, comments
    read_sbe21_hex            raw counts, lat/lon, depth
    apply_qc_flags            ship-speed and spike flags
    load_calibration          the file's own .xmlcon (else --xmlcon / the cruise folder .xmlcon)
    apply_calibration         (count conversion not yet implemented; counts pass through)

Output, in a single DuckDB file:
    processed/wf_flowthru.duckdb
        flowthru_data   one row per scan
        build_metadata  one row per build (git commit, calibration SHA-256, counts)
        flowthru_cnv    one row per hex file with a .cnv: name, SHA-256, rows matched, status
        flowthru_calibration  one row per hex file: .xmlcon used, SHA-256, T/C serials + cal dates

Per-cruise rows are replaced atomically inside one transaction.
"""

import argparse
import datetime as dt
import hashlib
import logging
import pathlib
import subprocess
import sys

import duckdb
import pandas as pd

import sbe21_ingestion

BASE_DIR = pathlib.Path(__file__).resolve().parent
DEFAULT_DB_PATH = BASE_DIR / "processed" / "wf_flowthru.duckdb"
LOG_DIR = BASE_DIR / "logs"


def setup_logging(cruise_id, verbose=False):
    """Per-build, timestamped log file plus console output."""
    LOG_DIR.mkdir(exist_ok=True)
    timestamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = LOG_DIR / f"wf_flowthru_build_{cruise_id}_{timestamp}.log"
    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s',
        handlers=[logging.FileHandler(log_path, mode='w'), logging.StreamHandler(sys.stdout)],
    )
    return log_path


def file_sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(65536), b''):
            h.update(chunk)
    return h.hexdigest()


def git_commit():
    try:
        out = subprocess.check_output(['git', '-C', str(BASE_DIR), 'rev-parse', 'HEAD'],
                                      stderr=subprocess.DEVNULL)
        return out.decode().strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return 'unknown'


def commit_to_duckdb(full_df, cruise_id, db_path, cal_rows, build_started, n_files, n_bad_lines, cnv_rows=()):
    """Replaces the cruise's rows atomically and records build provenance."""
    db_path.parent.mkdir(exist_ok=True)
    # One value for build_metadata: hash of the per-file hashes (None if no calibration at all).
    hashes = sorted(r[2] for r in cal_rows if r[2])
    cal_hash = hashlib.sha256(''.join(hashes).encode()).hexdigest() if hashes else None
    commit = git_commit()

    with duckdb.connect(str(db_path)) as con:
        con.register('df_view', full_df)
        con.execute("CREATE TABLE IF NOT EXISTS flowthru_data AS SELECT * FROM df_view WHERE 1=0")
        # An older database may lack columns added later (temp_c, cond_sm, sal_psu ...).
        existing = {r[0] for r in con.execute("DESCRIBE flowthru_data").fetchall()}
        for col in full_df.columns:
            if col not in existing:
                dtype = 'DOUBLE' if pd.api.types.is_float_dtype(full_df[col]) else None
                if dtype is None:
                    raise RuntimeError(f"flowthru_data has no column '{col}' and its type is not DOUBLE")
                con.execute(f'ALTER TABLE flowthru_data ADD COLUMN "{col}" {dtype}')
        con.execute("""
            CREATE TABLE IF NOT EXISTS flowthru_cnv (
                cruise_id VARCHAR, file_key VARCHAR, cnv_name VARCHAR, cnv_sha256 VARCHAR,
                datcnv_date VARCHAR, n_hex_scans BIGINT, n_cnv_rows BIGINT, n_matched BIGINT,
                n_position_mismatch BIGINT, match_method VARCHAR, status VARCHAR, columns VARCHAR
            )
        """)
        con.execute("""
            CREATE TABLE IF NOT EXISTS build_metadata (
                cruise_id          VARCHAR,
                build_started_ts   TIMESTAMP,
                build_finished_ts  TIMESTAMP,
                calibration_sha256 VARCHAR,
                git_commit         VARCHAR,
                files_processed    BIGINT,
                malformed_lines    BIGINT,
                rows_written       BIGINT
            )
        """)
        con.execute("""
            CREATE TABLE IF NOT EXISTS flowthru_calibration (
                cruise_id VARCHAR, file_key VARCHAR, xmlcon_name VARCHAR, xmlcon_sha256 VARCHAR,
                temp_serial VARCHAR, temp_cal_date VARCHAR,
                cond_serial VARCHAR, cond_cal_date VARCHAR,
                nmea_position VARCHAR, nmea_depth VARCHAR
            )
        """)
        con.execute("BEGIN TRANSACTION")
        try:
            con.execute("DELETE FROM flowthru_calibration WHERE cruise_id = ?", [cruise_id])
            for r in cal_rows:
                con.execute("INSERT INTO flowthru_calibration VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                            [cruise_id, *r])
            con.execute("DELETE FROM flowthru_cnv WHERE cruise_id = ?", [cruise_id])
            for r in cnv_rows:
                con.execute("INSERT INTO flowthru_cnv VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                            [cruise_id, *r])
            con.execute("DELETE FROM flowthru_data WHERE cruise_id = ?", [cruise_id])
            con.execute("INSERT INTO flowthru_data BY NAME SELECT * FROM df_view")
            con.execute("DELETE FROM build_metadata WHERE cruise_id = ?", [cruise_id])
            con.execute("INSERT INTO build_metadata VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        [cruise_id, build_started, dt.datetime.now(), cal_hash, commit,
                         n_files, n_bad_lines, len(full_df)])
            con.execute("COMMIT")
        except Exception:
            con.execute("ROLLBACK")
            raise


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="Build the Western Flyer flow-through DuckDB for one cruise.")
    p.add_argument("cruise_id", help="Cruise directory name under cruises/, e.g. 2025_Baja")
    p.add_argument("--db", type=pathlib.Path, default=DEFAULT_DB_PATH,
                   help=f"DuckDB output path (default: {DEFAULT_DB_PATH})")
    p.add_argument("--xmlcon", type=pathlib.Path, default=None,
                   help="SBE 21 .xmlcon (default: the single .xmlcon in cruises/<cruise_id>/, optional)")
    p.add_argument("--require-cnv", action="store_true",
                   help="Stop unless every .hex file has a matching SBE Data Processing .cnv")
    p.add_argument("--hex-dir", type=pathlib.Path, default=None,
                   help="Directory of raw .hex files (default: cruises/<cruise_id>/hex/)")
    p.add_argument("--verbose", "-v", action="store_true", help="DEBUG-level logging")
    return p.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    cruise_id = args.cruise_id
    cruise_dir = BASE_DIR / "cruises" / cruise_id

    (BASE_DIR / "processed").mkdir(exist_ok=True)
    log_path = setup_logging(cruise_id, verbose=args.verbose)

    if not cruise_dir.exists():
        logging.error("Cruise directory '%s' not found.", cruise_dir)
        sys.exit(1)

    build_started = dt.datetime.now()
    logging.info("--- STARTING FLOW-THROUGH PIPELINE FOR %s ---", cruise_id.upper())
    logging.info("Build log: %s", log_path)

    config = sbe21_ingestion.load_config(cruise_dir / 'config.toml')
    config['CRUISE_ID'] = cruise_id

    # Calibration source, in order: --xmlcon (applies to every file), the .XMLCON saved beside
    # each .hex file, then the single .xmlcon in the cruise folder as a fallback.
    override = sbe21_ingestion.load_calibration(args.xmlcon) if args.xmlcon else None
    if args.xmlcon and override is None:
        logging.error("--xmlcon file not found: %s", args.xmlcon)
        sys.exit(1)
    # Cruise-level .xmlcon file(s).  With several (e.g. Baja: NMEA depth off / on), each hex
    # file uses the one whose NmeaDepthDataAdded setting matches its scan-line length.
    cruise_cals = [sbe21_ingestion.load_calibration(p) for p in sorted(cruise_dir.iterdir())
                   if p.is_file() and p.suffix.lower() == '.xmlcon']

    hex_dir = args.hex_dir if args.hex_dir else cruise_dir / 'hex'
    if not hex_dir.exists():
        logging.error("Hex directory not found: %s", hex_dir)
        sys.exit(1)
    files = sorted(hex_dir.glob("*.hex"))
    if not files:
        logging.warning("No .hex files found in %s", hex_dir)

    # .cnv files from SBE Data Processing (temperature, conductivity, salinity ...).
    # Searched in cruise_dir/cnv, then cruise_dir, then the hex folder.
    cnv_dirs = [cruise_dir / 'cnv', cruise_dir, hex_dir]
    cnv_for = {fp: sbe21_ingestion.find_matching_cnv(fp, cnv_dirs) for fp in files}
    missing = [fp.name for fp, c in cnv_for.items() if c is None]
    if args.require_cnv and missing:
        logging.error("No .cnv found for %d file(s): %s", len(missing), ", ".join(missing))
        sys.exit(1)
    if not any(cnv_for.values()):
        logging.info("No .cnv files found: output is raw counts.")

    frames, failures, n_bad, cal_rows, cnv_rows = [], [], 0, [], []
    for fp in files:
        logging.info("Processing: %s", fp.name)
        try:
            if override is not None:
                calibration = override
            else:
                own = sbe21_ingestion.find_matching_xmlcon(fp)
                if own:
                    calibration = sbe21_ingestion.load_calibration(own)
                elif len(cruise_cals) <= 1:
                    calibration = cruise_cals[0] if cruise_cals else None
                else:
                    want = '1' if sbe21_ingestion.hex_has_depth(fp) else '0'
                    cands = [c for c in cruise_cals if c['nmea_depth'] == want]
                    if len(cands) != 1:
                        raise ValueError(f"{len(cands)} cruise .xmlcon files have NMEA depth = {want} "
                                         f"({', '.join(c['name'] for c in cruise_cals)}); cannot choose.")
                    calibration = cands[0]
            if calibration is None:
                logging.warning("  no .xmlcon for %s: RAW COUNTS only.", fp.name)
            else:
                logging.info("  calibration: %s", calibration['name'])
                by_type = {}
                for s_ in calibration['sensors']:
                    if s_['serial']:  # skip empty slots (e.g. the unused 2nd temperature sensor)
                        by_type.setdefault(s_['type'], s_)
                t = by_type.get('TemperatureSensor', {})
                c = by_type.get('ConductivitySensor', {})
                cal_rows.append((fp.stem, calibration['name'], calibration['sha256'],
                                 t.get('serial'), t.get('cal_date'), c.get('serial'), c.get('cal_date'),
                                 calibration['nmea_position'], calibration['nmea_depth']))
            df = sbe21_ingestion.ingest_sbe21_hex(fp, config, cruise_id=cruise_id,
                                                  calibration=calibration, cnv_path=cnv_for[fp])
            ci = df.attrs.get('cnv')
            if ci:
                log = logging.info if ci['status'].startswith('applied') and ci['n_matched'] >= ci['n_hex_scans'] else logging.warning
                log("  cnv: %s -> %s (%d .cnv rows, %d of %d scans matched, %d position mismatches, method %s)",
                    ci['cnv_name'], ci['status'], ci['n_cnv_rows'], ci['n_matched'], ci['n_hex_scans'],
                    ci['n_position_mismatch'], ci['match_method'])
                cnv_rows.append((fp.stem, ci['cnv_name'], ci['cnv_sha256'], ci['datcnv_date'],
                                 ci['n_hex_scans'], ci['n_cnv_rows'], ci['n_matched'],
                                 ci['n_position_mismatch'], ci['match_method'], ci['status'], ci['columns']))
            else:
                logging.info("  no .cnv for %s (raw counts only)", fp.name)
            bad = df.attrs.get('n_bad_lines', 0)
            n_bad += bad
            flagged = int((df['qc_flag'] & (sbe21_ingestion.QC_SPIKE | sbe21_ingestion.QC_SPEED
                                           | sbe21_ingestion.QC_BAD_TSC)).astype(bool).sum())
            logging.info("  %d scans, %s -> %s, %d malformed line(s), %d QC-flagged",
                         len(df), df['time_utc'].iloc[0], df['time_utc'].iloc[-1], bad, flagged)
            frames.append(df)
        except Exception as exc:
            logging.exception("Failed to process %s: %s", fp.name, exc)
            failures.append(fp.name)

    if not frames:
        logging.warning("No data processed; database not updated.")
        sys.exit(1 if failures else 0)

    full_df = pd.concat(frames, ignore_index=True)
    logging.info("Committing %d scans to %s", len(full_df), args.db)
    commit_to_duckdb(full_df, cruise_id, args.db, cal_rows, build_started, len(frames), n_bad, cnv_rows)

    if failures:
        logging.warning("Finished with %d failed file(s): %s", len(failures), failures)
    else:
        logging.info("Pipeline finished successfully.")


if __name__ == "__main__":
    main()
