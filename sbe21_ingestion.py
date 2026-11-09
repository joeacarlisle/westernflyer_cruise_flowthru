"""Sea-Bird SBE 21 SeaCAT thermosalinograph (flow-through) ingestion.

``main.py`` builds the database from SBE Data Processing .cnv files only (see
``ingest_sbe21_cnv`` at the end of this module).  The .cnv values are used exactly as
written; no .hex or .xmlcon file is read.

There is no pressure sensor, so none of the profiling-CTD steps (soak, loop edit,
depth binning) apply; the data are a time series along the track.

The raw .hex scan format (used before the pipeline moved to .cnv files) is described
in docs/sbe21_hex_format.md.
"""

import hashlib
import logging
import re
from pathlib import Path

import numpy as np
import pandas as pd


# qc_flag bit values (bit 4 is unused)
QC_SPIKE = 1                 # converted temperature / salinity departs from its running median
QC_SPEED = 2                 # implied ship speed from positions is implausible
QC_BAD_POSITION = 8          # position outside the header position +/- tolerance
QC_BAD_TSC = 16              # converted temperature / salinity implausible (cell likely not flowing seawater)


# ---------------------------------------------------------------------------
# Config / metadata helpers (same conventions as the SBE 19plus pipeline)
# ---------------------------------------------------------------------------

def load_config(file_path):
    """Loads processing parameters from a TOML file into one flat dict.

    Sections are flattened so ``config.get('KEY', default)`` works anywhere.
    Duplicate keys across sections raise instead of silently overwriting.
    Values are kept as strings (callers convert), matching the CTD pipeline.
    """
    try:
        import tomllib
    except ImportError:          # Python < 3.11
        import tomli as tomllib
    file_path = Path(file_path)
    logging.info("Loading configuration: %s", file_path)
    with open(file_path, 'rb') as f:
        raw = tomllib.load(f)
    flat = {}
    for section, value in raw.items():
        if isinstance(value, dict):
            for k, v in value.items():
                if k in flat:
                    raise ValueError(f"Duplicate key '{k}' in {file_path}")
                flat[k] = str(v)
        else:
            flat[section] = str(value)
    return flat


def _parse_header_time(text):
    """'Apr 16 2025  04:30:13' (any spacing) -> pd.Timestamp, or NaT."""
    if not text:
        return pd.NaT
    text = re.sub(r'\s+', ' ', text.strip())
    return pd.to_datetime(text, format='%b %d %Y %H:%M:%S', errors='coerce')


def _parse_header_dm(text):
    """'24 12.9429 N' -> signed decimal degrees, or None."""
    if not text:
        return None
    m = re.match(r'^\s*(\d+)\s+(\d+(?:\.\d+)?)\s*([NSEW])?\s*$', text)
    if not m:
        return None
    deg, minutes, hemi = m.groups()
    val = float(deg) + float(minutes) / 60.0
    return -val if hemi in ('S', 'W') else val


# ---------------------------------------------------------------------------
# QC helpers
# ---------------------------------------------------------------------------

def _haversine_m(lat1, lon1, lat2, lon2):
    r = 6371000.0
    p1, p2 = np.radians(lat1), np.radians(lat2)
    dphi = p2 - p1
    dlmb = np.radians(lon2 - lon1)
    a = np.sin(dphi / 2) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(dlmb / 2) ** 2
    return 2 * r * np.arcsin(np.sqrt(a))


# ---------------------------------------------------------------------------
# SBE Data Processing .cnv import
# ---------------------------------------------------------------------------
# Temperature / conductivity / salinity are NOT computed here.  They come from
# .cnv files written by SBE Data Processing (Data Conversion, optionally Derive).

# .cnv short name -> output column.  Other variables are ignored.
CNV_COLUMNS = {
    't090C': 'temp_c',          # Temperature [ITS-90, deg C]
    'tv290C': 'temp_c',
    'c0S/m': 'cond_sm',         # Conductivity [S/m]
    'sal00': 'sal_psu',         # Salinity, Practical [PSU]
    'flECO-AFL': 'fluor_mgm3',  # Fluorescence, WET Labs ECO-AFL/FL [mg/m^3]
    'svCF': 'sound_velocity_fts',   # Sound velocity [Chen-Millero, ft/s] as written by SBE Data Processing
    'CStarAt0': 'cstar_atten_m',    # Beam attenuation, WET Labs C-Star [1/m]
    'CStarTr0': 'cstar_trans_pct',  # Beam transmission, WET Labs C-Star [%]
}


def read_cnv(cnv_path):
    """Parses an SBE Data Processing .cnv (ASCII) into (DataFrame, meta).

    Columns are the .cnv short names (``t090C``, ``c0S/m``, ``sal00``, ``scan`` ...).
    Values equal to the file's bad_flag become NaN.  ``meta`` has the header
    variable list, bad_flag, datcnv_date and the declared nvalues.
    """
    cnv_path = Path(cnv_path)
    names, bad_flag, meta = {}, -9.99e-29, {'datcnv_date': '', 'nvalues': None}
    rows = []
    in_header = True
    with open(cnv_path, 'r', errors='replace') as f:
        for line in f:
            s = line.rstrip('\r\n')
            if in_header:
                if s.startswith('*END*'):
                    in_header = False
                    continue
                m = re.match(r'#\s*name\s+(\d+)\s*=\s*([^:]+):\s*(.*)$', s)
                if m:
                    names[int(m.group(1))] = (m.group(2).strip(), m.group(3).strip())
                    continue
                m = re.match(r'#\s*bad_flag\s*=\s*(\S+)', s)
                if m:
                    bad_flag = float(m.group(1))
                    continue
                m = re.match(r'#\s*nvalues\s*=\s*(\d+)', s)
                if m:
                    meta['nvalues'] = int(m.group(1))
                    continue
                m = re.match(r'#\s*datcnv_date\s*=\s*(.*)$', s)
                if m:
                    meta['datcnv_date'] = m.group(1).strip()
                continue
            if s.strip():
                rows.append(s.split())
    if not names:
        raise ValueError(f"{cnv_path.name}: no '# name' variable lines found; not a .cnv?")
    cols = [names[i][0] for i in sorted(names)]
    width = len(cols)
    good = [r for r in rows if len(r) == width]
    df = pd.DataFrame(good, columns=cols).apply(pd.to_numeric, errors='coerce')
    df = df.mask(np.isclose(df, bad_flag, rtol=1e-6, atol=0))
    meta['variables'] = {names[i][0]: names[i][1] for i in sorted(names)}
    meta['n_rows'] = len(df)
    meta['n_bad_rows'] = len(rows) - len(good)
    return df, meta


def flag_implausible_tsc(df, config):
    """Sets QC_BAD_TSC where the converted values cannot be seawater in the cell.

    Limits come from config.toml ([qc] MIN_SALINITY_PSU, MIN_TEMP_C, MAX_TEMP_C;
    defaults 20, -2, 40).  Only scans that have converted values are tested;
    the data are flagged, never removed.
    """
    if 'sal_psu' not in df.columns and 'temp_c' not in df.columns:
        return df
    min_sal = float(config.get('MIN_SALINITY_PSU', 20.0))
    min_t = float(config.get('MIN_TEMP_C', -2.0))
    max_t = float(config.get('MAX_TEMP_C', 40.0))
    bad = np.zeros(len(df), dtype=bool)
    if 'sal_psu' in df.columns:
        bad |= (df['sal_psu'] < min_sal).to_numpy()
    if 'temp_c' in df.columns:
        bad |= ((df['temp_c'] < min_t) | (df['temp_c'] > max_t)).to_numpy()
    df['qc_flag'] = df['qc_flag'].to_numpy() | np.where(bad, QC_BAD_TSC, 0)
    return df


def flag_converted_spikes(df, config):
    """Spike test on converted temperature and salinity (QC_SPIKE).

    For scans that have converted values, the raw-count spike flag is replaced by
    a test on the converted values: temperature or salinity departing from its
    running median (window SPIKE_WINDOW) by more than SPIKE_TEMP_C (default 0.2 C)
    or SPIKE_SAL_PSU (default 0.5 PSU).  Scans without converted values keep the
    count-based flag.  Flags only; nothing is removed.
    """
    if 'temp_c' not in df.columns or 'sal_psu' not in df.columns:
        return df
    win = int(config.get('SPIKE_WINDOW', 9))
    thr_t = float(config.get('SPIKE_TEMP_C', 0.2))
    thr_s = float(config.get('SPIKE_SAL_PSU', 0.5))
    dev_t = (df['temp_c'] - df['temp_c'].rolling(win, center=True, min_periods=1).median()).abs()
    dev_s = (df['sal_psu'] - df['sal_psu'].rolling(win, center=True, min_periods=1).median()).abs()
    converted = (df['temp_c'].notna() & df['sal_psu'].notna()).to_numpy()
    spike = (((dev_t > thr_t) | (dev_s > thr_s)).to_numpy()) & converted
    q = df['qc_flag'].to_numpy().copy()
    q[converted] &= ~QC_SPIKE
    df['qc_flag'] = q | np.where(spike, QC_SPIKE, 0)
    return df


# ---------------------------------------------------------------------------
# .cnv-only ingestion (the pipeline's data source)
# ---------------------------------------------------------------------------
# Everything comes from the SBE Data Processing .cnv: scans, time, position and the
# converted variables.  Values are used as written (only the file's own bad_flag
# sentinel becomes NaN).  Ship speed and QC flags are derived from them; nothing is
# removed.  Depth is not in a .cnv, so depth_m is NaN.

def find_cnv_files(search_dirs):
    """.cnv files (sorted) from the first directory in ``search_dirs`` that has any."""
    for d in search_dirs:
        d = Path(d)
        if d.is_dir():
            found = sorted(p for p in d.iterdir() if p.is_file() and p.suffix.lower() == '.cnv')
            if found:
                return found
    return []


def parse_cnv_header(cnv_path):
    """Reads the ``*`` / ``**`` / ``#`` header of a .cnv (up to ``*END*``).

    Returns a dict: start_time, start_source, nmea_utc, system_utc,
    upload_time_local, header_lat, header_lon, interval_s, nvalues, datcnv_date,
    station, operator, notes, temp_serial, temp_cal_date, cond_serial,
    cond_cal_date, calibration_sha256 (hash of the sensor-configuration XML the
    .cnv header embeds; None if absent).
    """
    info = {
        'start_time': pd.NaT, 'start_source': '', 'nmea_utc': pd.NaT, 'system_utc': pd.NaT,
        'upload_time_local': pd.NaT, 'header_lat': None, 'header_lon': None,
        'interval_s': None, 'nvalues': None, 'datcnv_date': '',
        'station': '', 'operator': '', 'notes': [],
        'temp_serial': None, 'temp_cal_date': None, 'cond_serial': None, 'cond_cal_date': None,
        'calibration_sha256': None,
    }
    xml, sensor = [], None
    with open(cnv_path, 'r', errors='replace') as f:
        for line in f:
            s = line.rstrip('\r\n')
            if s.startswith('*END*'):
                break
            if s.startswith('**'):
                body = s[2:].strip()
                low = body.lower()
                if low.startswith('station:'):
                    info['station'] = body.split(':', 1)[1].strip()
                elif low.startswith('operator:'):
                    info['operator'] = body.split(':', 1)[1].strip()
                elif low.startswith('ship:'):
                    pass
                elif body:
                    info['notes'].append(body)
                continue
            if s.startswith('*'):
                if '=' in s:
                    key, val = [p.strip() for p in s[1:].split('=', 1)]
                    if key == 'System UpLoad Time':
                        info['upload_time_local'] = _parse_header_time(val)
                    elif key == 'NMEA UTC (Time)':
                        info['nmea_utc'] = _parse_header_time(val)
                    elif key == 'System UTC':
                        info['system_utc'] = _parse_header_time(val)
                    elif key == 'NMEA Latitude':
                        info['header_lat'] = _parse_header_dm(val)
                    elif key == 'NMEA Longitude':
                        info['header_lon'] = _parse_header_dm(val)
                continue
            if not s.startswith('#'):
                continue
            body = s[1:].strip()
            if body.startswith('<'):
                xml.append(body)
                if body.startswith('<TemperatureSensor'):
                    sensor = 'temp'
                elif body.startswith('<ConductivitySensor'):
                    sensor = 'cond'
                elif body.startswith('</TemperatureSensor') or body.startswith('</ConductivitySensor'):
                    sensor = None
                elif sensor:
                    m = re.match(r'<SerialNumber>([^<]*)</SerialNumber>', body)
                    if m and info[f'{sensor}_serial'] is None:
                        info[f'{sensor}_serial'] = m.group(1).strip()
                    m = re.match(r'<CalibrationDate>([^<]*)</CalibrationDate>', body)
                    if m and info[f'{sensor}_cal_date'] is None:
                        info[f'{sensor}_cal_date'] = m.group(1).strip()
                continue
            m = re.match(r'start_time\s*=\s*(.*?)\s*(?:\[(.*)\])?\s*$', body)
            if m:
                info['start_time'] = _parse_header_time(m.group(1))
                info['start_source'] = (m.group(2) or '').strip()
                continue
            m = re.match(r'interval\s*=\s*seconds:\s*([\d.]+)', body)
            if m:
                info['interval_s'] = float(m.group(1))
                continue
            m = re.match(r'nvalues\s*=\s*(\d+)', body)
            if m:
                info['nvalues'] = int(m.group(1))
                continue
            m = re.match(r'datcnv_date\s*=\s*(.*)$', body)
            if m:
                info['datcnv_date'] = m.group(1).strip()
    if xml:
        info['calibration_sha256'] = hashlib.sha256('\n'.join(xml).encode('utf-8')).hexdigest()
    return info


def apply_cnv_qc_flags(df, t_s, config, header_lat=None, header_lon=None):
    """Adds ``speed_ms`` and ``qc_flag`` using only values that are in the .cnv.

    Speed is the distance between positions ``SPEED_LAG_SCANS`` apart over the
    elapsed time (Seasave latches the last NMEA fix, so adjacent scans repeat a
    position).  Flags are set, never removed: QC_SPEED (speed > MAX_SPEED_MS),
    QC_BAD_POSITION (first scan far from the header NMEA position), then the
    converted-value tests QC_BAD_TSC and QC_SPIKE.
    """
    max_speed = float(config.get('MAX_SPEED_MS', 10.0))
    lag = max(int(config.get('SPEED_LAG_SCANS', 10)), 1)
    pos_tol = float(config.get('POSITION_TOL_DEG', 1.0))

    lat, lon = df['lat'].to_numpy(float), df['lon'].to_numpy(float)
    speed = np.full(len(df), np.nan)
    if len(df) > lag:
        dist = _haversine_m(lat[:-lag], lon[:-lag], lat[lag:], lon[lag:])
        with np.errstate(divide='ignore', invalid='ignore'):
            speed[lag:] = dist / (t_s[lag:] - t_s[:-lag])
    df['speed_ms'] = speed

    flag = np.zeros(len(df), dtype=np.int64)
    flag[np.nan_to_num(speed, nan=0.0) > max_speed] |= QC_SPEED
    if header_lat is not None and header_lon is not None and len(df):
        if abs(lat[0] - header_lat) > pos_tol or abs(lon[0] - header_lon) > pos_tol:
            flag[0] |= QC_BAD_POSITION
    df['qc_flag'] = flag
    df = flag_implausible_tsc(df, config)
    df = flag_converted_spikes(df, config)
    return df


def ingest_sbe21_cnv(cnv_path, config, cruise_id=None):
    """Converts one SBE Data Processing .cnv to a per-scan DataFrame.

    Columns: cruise_id, file_key (the .cnv file name without extension), scan_index
    (.cnv ``scan`` - 1), time_utc (header start_time + ``timeS``), lat, lon, depth_m
    (always NaN), the converted variables in CNV_COLUMNS, speed_ms, qc_flag,
    station_name, operator, sample_interval_s.  ``df.attrs`` carries ``header``,
    ``n_bad_lines`` (malformed data rows), ``cnv`` (row for the flowthru_cnv table)
    and ``calibration`` (row for flowthru_calibration, from the .cnv header).
    """
    cnv_path = Path(cnv_path)
    hdr = parse_cnv_header(cnv_path)
    cnv, meta = read_cnv(cnv_path)
    if cnv.empty:
        raise ValueError(f"{cnv_path.name}: no data rows in .cnv.")
    for need in ('timeS', 'latitude', 'longitude'):
        if need not in cnv.columns:
            raise ValueError(f"{cnv_path.name}: .cnv has no '{need}' column.")

    start = hdr['start_time']
    if pd.isnull(start):
        start = hdr['nmea_utc'] if pd.notnull(hdr['nmea_utc']) else hdr['system_utc']
        logging.warning("%s: no start_time line in .cnv header; used the header UTC time.", cnv_path.name)
    if pd.isnull(start):
        raise ValueError(f"{cnv_path.name}: no usable start time in .cnv header.")

    t_s = cnv['timeS'].to_numpy(float)
    if 'scan' in cnv.columns and cnv['scan'].notna().all():
        scan_index = cnv['scan'].astype(int).to_numpy() - 1
    else:
        scan_index = np.arange(len(cnv))
    interval = hdr['interval_s'] if hdr['interval_s'] else (float(np.nanmedian(np.diff(t_s))) if len(t_s) > 1 else 3.0)

    df = pd.DataFrame({
        'cruise_id': cruise_id or '',
        'file_key': cnv_path.stem,
        'scan_index': scan_index,
        'time_utc': start + pd.to_timedelta(t_s, unit='s'),
        'lat': cnv['latitude'].to_numpy(float),
        'lon': cnv['longitude'].to_numpy(float),
        'depth_m': np.nan,
    })
    cols = []
    for short, col in CNV_COLUMNS.items():
        if short in cnv.columns and col not in cols:
            df[col] = cnv[short].to_numpy(float)
            cols.append(col)

    df = apply_cnv_qc_flags(df, t_s, config, hdr['header_lat'], hdr['header_lon'])
    df['station_name'] = hdr['station']
    df['operator'] = hdr['operator']
    df['sample_interval_s'] = interval

    n_rows, n_bad = len(cnv), meta['n_bad_rows']
    declared = hdr['nvalues']
    if n_bad == 0 and (declared is None or declared == n_rows):
        status = 'loaded'
    else:
        status = f"loaded {n_rows} rows (header declares {declared}; {n_bad} malformed rows skipped)"
    df.attrs['header'] = hdr
    df.attrs['n_bad_lines'] = n_bad
    df.attrs['cnv'] = {
        'cnv_name': cnv_path.name,
        'cnv_sha256': hashlib.sha256(cnv_path.read_bytes()).hexdigest(),
        'datcnv_date': hdr['datcnv_date'],
        'n_declared': declared, 'n_cnv_rows': n_rows, 'n_loaded': n_rows,
        'status': status, 'columns': ','.join(cols),
    }
    if hdr['calibration_sha256']:
        df.attrs['calibration'] = ('(embedded in .cnv header)', hdr['calibration_sha256'],
                                   hdr['temp_serial'], hdr['temp_cal_date'],
                                   hdr['cond_serial'], hdr['cond_cal_date'], None, None)
    else:
        df.attrs['calibration'] = None
    return df
