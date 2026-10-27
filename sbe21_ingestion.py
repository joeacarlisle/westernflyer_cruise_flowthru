"""Sea-Bird SBE 21 SeaCAT thermosalinograph (flow-through) .hex ingestion.

Reads raw Seasave V7 .hex files from the Western Flyer flow-through system
(SBE 21, S/N 3507) and returns one row per scan with:

    * raw channel counts (no calibration applied -- see below)
    * NMEA latitude / longitude appended to every scan by Seasave
    * NMEA depth (only present in the longer row format)
    * a UTC timestamp for every scan
    * basic QC flags

There is no pressure sensor, so none of the profiling-CTD steps (soak,
loop edit, depth binning) apply; the data are a time series along the track.

Row formats seen in baja2025 (all hex, one scan per line, after ``*END*``)::

    40 characters                      46 characters (adds NMEA depth)
    [ 0: 4] ch0 count                  same
    [ 4: 8] ch1 count                  same
    [ 8:12] ch2 count                  same
    [12:16] ch3 raw word               same
    [16:20] ch4 raw word               same
    [20:24] ch5 raw word (always 0)    same
    [24:26] aux byte                   same
    [26:32] latitude  = int / 50000    same   (degrees, sign from header)
    [32:38] longitude = int / 50000    same   (degrees, sign from header)
    [38:40] flag byte (0x40 / 0x41)    same
                                       [40:46] NMEA depth, decimetres (0 = none)

Status of the field decoding:
    VERIFIED   latitude, longitude (match the header NMEA position)
    PROBABLE   depth (plausible metres, missing while the EK80 is running)
    UNKNOWN    ch0..ch5 and aux byte.  ch0 / ch1 behave like temperature and
               conductivity counts and ch2 tracks ch0, but this is NOT
               confirmed.  Conversion to engineering units needs the SBE 21
               calibration (.xmlcon / .cal) for S/N 3507 and is deliberately
               not implemented here -- no coefficients are guessed.
"""

import hashlib
import logging
import xml.etree.ElementTree as ET
import re
from pathlib import Path

import numpy as np
import pandas as pd

ROW_LEN_BASIC = 40
ROW_LEN_DEPTH = 46
POS_SCALE = 50000.0          # lat/lon words are degrees * 50000
DEPTH_SCALE = 10.0           # depth word is decimetres (probable)

# qc_flag bit values
QC_SPIKE = 1                 # channel count departs from its running median
QC_SPEED = 2                 # implied ship speed from positions is implausible
QC_NO_DEPTH = 4              # no NMEA depth for this scan (informational)
QC_BAD_POSITION = 8          # position outside the header position +/- tolerance
QC_BAD_TSC = 16              # converted temperature / salinity implausible (cell likely not flowing seawater)

_HEX_RE = re.compile(r'^[0-9A-Fa-f]+$')


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


def parse_hex_header(hex_path):
    """Reads the ``*`` header block of a Seasave .hex file (up to ``*END*``).

    Returns a dict with: instrument, software, serial, upload_time_local,
    nmea_utc, system_utc, header_lat, header_lon, sample_interval_s,
    station, operator, notes (list), n_header_lines.
    """
    info = {
        'instrument': None, 'software': None, 'serial': None,
        'upload_time_local': pd.NaT, 'nmea_utc': pd.NaT, 'system_utc': pd.NaT,
        'header_lat': None, 'header_lon': None, 'sample_interval_s': None,
        'station': '', 'operator': '', 'notes': [], 'n_header_lines': 0,
    }
    with open(hex_path, 'r', errors='replace') as f:
        for i, line in enumerate(f):
            s = line.strip()
            info['n_header_lines'] = i + 1
            if s.startswith('*END*'):
                break
            if s.startswith('**'):
                body = s[2:].strip()
                if body.lower().startswith('station:'):
                    info['station'] = body.split(':', 1)[1].strip()
                elif body.lower().startswith('operator:'):
                    info['operator'] = body.split(':', 1)[1].strip()
                elif body.lower().startswith('ship:'):
                    pass
                elif body:
                    info['notes'].append(body)
                continue
            if s.startswith('* Sea-Bird') and 'Data File' in s:
                info['instrument'] = s.lstrip('* ').replace(' Data File:', '').strip()
            elif s.startswith('* Software Version'):
                info['software'] = s.replace('* Software Version', '').strip()
            elif '=' in s:
                key, val = [p.strip() for p in s.lstrip('* ').split('=', 1)]
                if key == 'Temperature SN':
                    info['serial'] = val
                elif key == 'System UpLoad Time':
                    info['upload_time_local'] = _parse_header_time(val)
                elif key == 'NMEA UTC (Time)':
                    info['nmea_utc'] = _parse_header_time(val)
                elif key == 'System UTC':
                    info['system_utc'] = _parse_header_time(val)
                elif key == 'NMEA Latitude':
                    info['header_lat'] = _parse_header_dm(val)
                elif key == 'NMEA Longitude':
                    info['header_lon'] = _parse_header_dm(val)
                elif key == 'Real-Time Sample Interval':
                    m = re.match(r'([\d.]+)', val)
                    if m:
                        info['sample_interval_s'] = float(m.group(1))
    return info


# ---------------------------------------------------------------------------
# Raw hex reader
# ---------------------------------------------------------------------------

def read_sbe21_hex(hex_path, n_header_lines):
    """Decodes the data block into a DataFrame of raw integers.

    ``scan_index`` counts EVERY data line, including malformed ones, so a
    truncated line (logging restart) still consumes its time slot.  Malformed
    lines are dropped from the returned frame and reported in ``attrs``.
    """
    with open(hex_path, 'r', errors='replace') as f:
        lines = f.read().replace('\r', '').split('\n')[n_header_lines:]
    lines = [ln.strip() for ln in lines if ln.strip()]

    idx, rows, depth_words, bad = [], [], [], []
    for i, ln in enumerate(lines):
        n = len(ln)
        if n not in (ROW_LEN_BASIC, ROW_LEN_DEPTH) or not _HEX_RE.match(ln):
            bad.append(i)
            continue
        idx.append(i)
        rows.append((
            int(ln[0:4], 16), int(ln[4:8], 16), int(ln[8:12], 16),
            int(ln[12:16], 16), int(ln[16:20], 16), int(ln[20:24], 16),
            int(ln[24:26], 16),
            int(ln[26:32], 16), int(ln[32:38], 16), int(ln[38:40], 16),
        ))
        depth_words.append(int(ln[40:46], 16) if n == ROW_LEN_DEPTH else -1)

    cols = ['ch0_counts', 'ch1_counts', 'ch2_counts',
            'ch3_raw', 'ch4_raw', 'ch5_raw', 'aux_byte',
            'lat_word', 'lon_word', 'flag_byte']
    df = pd.DataFrame(rows, columns=cols)
    df.insert(0, 'scan_index', idx)
    df['depth_word'] = depth_words
    df.attrs['n_lines'] = len(lines)
    df.attrs['bad_line_indices'] = bad
    return df


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


def apply_qc_flags(df, config, header_lat=None, header_lon=None):
    """Adds ``speed_ms`` and a bitmask ``qc_flag`` (0 = no flag raised).

    Thresholds come from config.toml ([qc]); the spike threshold is provisional
    until the channels are identified and converted.
    """
    max_speed = float(config.get('MAX_SPEED_MS', 10.0))
    win = int(config.get('SPIKE_WINDOW', 9))
    thresh = float(config.get('SPIKE_COUNTS', 300))
    pos_tol = float(config.get('POSITION_TOL_DEG', 1.0))

    flag = np.zeros(len(df), dtype=np.int64)

    # Ship speed implied by positions ``lag`` scans apart.  Seasave latches the
    # last NMEA fix onto each scan, so consecutive scans often repeat a position
    # and then jump; a multi-scan baseline removes that stair-step artefact.
    lag = max(int(config.get('SPEED_LAG_SCANS', 10)), 1)
    lat, lon = df['lat'].to_numpy(), df['lon'].to_numpy()
    t = (df['time_utc'] - df['time_utc'].iloc[0]).dt.total_seconds().to_numpy()   # unit-independent (pandas 2 ns / 3 us)
    speed = np.full(len(df), np.nan)
    if len(df) > lag:
        dist = _haversine_m(lat[:-lag], lon[:-lag], lat[lag:], lon[lag:])
        with np.errstate(divide='ignore', invalid='ignore'):
            speed[lag:] = dist / (t[lag:] - t[:-lag])
    df['speed_ms'] = speed
    flag[np.nan_to_num(speed, nan=0.0) > max_speed] |= QC_SPEED

    # Running-median spike test on the two channels that look like T and C.
    for col in ('ch0_counts', 'ch1_counts'):
        med = df[col].rolling(win, center=True, min_periods=1).median()
        flag[(df[col] - med).abs().to_numpy() > thresh] |= QC_SPIKE

    flag[df['depth_m'].isna().to_numpy()] |= QC_NO_DEPTH

    if header_lat is not None and header_lon is not None:
        off = ((df['lat'] - header_lat).abs() > pos_tol) | ((df['lon'] - header_lon).abs() > pos_tol)
        # Only meaningful near the start of a file; ships move, so test the
        # first scan only.
        if len(df) and off.iloc[0]:
            flag[0] |= QC_BAD_POSITION

    df['qc_flag'] = flag
    return df


# ---------------------------------------------------------------------------
# Calibration hook
# ---------------------------------------------------------------------------

def find_matching_xmlcon(hex_path):
    """Returns the .xmlcon saved with ``hex_path`` (same stem, any case), or None.

    Seasave writes one configuration file per logging session next to the
    .hex file (e.g. 20250916.hex + 20250916.XMLCON), so each data file is
    paired with its own configuration.
    """
    hex_path = Path(hex_path)
    stem = hex_path.stem.lower()
    for cand in sorted(hex_path.parent.iterdir()):
        if cand.suffix.lower() == '.xmlcon' and cand.stem.lower() == stem:
            return cand
    return None


def hex_has_depth(hex_path, max_lines=500):
    """True if the file uses the 46-character scan format (NMEA depth appended).

    Looks at the longest of the first ``max_lines`` data lines, so a truncated
    first line (seen in some files) does not hide the format.
    """
    longest = 0
    with open(hex_path, 'r', errors='replace') as f:
        seen_end, n = False, 0
        for line in f:
            s = line.strip()
            if not seen_end:
                seen_end = s.startswith('*END*')
                continue
            if s:
                longest = max(longest, len(s))
                n += 1
                if n >= max_lines:
                    break
    return longest >= ROW_LEN_DEPTH


def _xml_text(el, tag):
    child = el.find(tag)
    return child.text.strip() if child is not None and child.text else ''


def load_calibration(xmlcon_path):
    """Parses an SBE 21 .xmlcon into a dict (no conversion is applied).

    Returns None when the file is missing.  The dict holds the file name,
    SHA-256, instrument NMEA flags and, for every sensor slot, its type,
    serial number, calibration date and raw coefficients.  No coefficients are
    assumed or defaulted.
    """
    if xmlcon_path is None or not Path(xmlcon_path).exists():
        return None
    xmlcon_path = Path(xmlcon_path)
    root = ET.parse(xmlcon_path).getroot()
    inst = root.find('Instrument')
    cal = {
        'path': xmlcon_path,
        'name': xmlcon_path.name,
        'sha256': hashlib.sha256(xmlcon_path.read_bytes()).hexdigest(),
        'instrument': _xml_text(inst, 'Name') if inst is not None else '',
        'nmea_position': _xml_text(inst, 'NmeaPositionDataAdded'),
        'nmea_depth': _xml_text(inst, 'NmeaDepthDataAdded'),
        'nmea_time': _xml_text(inst, 'NmeaTimeAdded'),
        'sensors': [],
    }
    for sensor in root.iter('Sensor'):
        body = next(iter(sensor), None)
        if body is None:
            continue
        cal['sensors'].append({
            'index': int(sensor.get('index', -1)),
            'sensor_id': sensor.get('SensorID'),
            'type': body.tag,
            'serial': _xml_text(body, 'SerialNumber'),
            'cal_date': _xml_text(body, 'CalibrationDate'),
            'coefficients': {c.tag: (c.text or '').strip() for c in body
                             if c.tag not in ('SerialNumber', 'CalibrationDate')},
        })
    cal['sensors'].sort(key=lambda x: x['index'])
    return cal


def apply_calibration(df, calibration):
    """Placeholder: count -> temperature / conductivity / salinity conversion.

    ``calibration`` is the parsed .xmlcon for THIS file.  The conversion is not
    implemented (channel layout is unconfirmed), so counts pass through.
    """
    if calibration is not None:
        logging.debug("%s loaded; SBE 21 count conversion not implemented, output stays raw counts.",
                      calibration['name'])
    return df


# ---------------------------------------------------------------------------
# SBE Data Processing .cnv import
# ---------------------------------------------------------------------------
# Temperature / conductivity / salinity are NOT computed here.  They come from
# .cnv files written by SBE Data Processing (Data Conversion, optionally
# Derive) and are matched to the .hex scans.

# .cnv short name -> output column.  Other variables are ignored.
CNV_COLUMNS = {
    't090C': 'temp_c',          # Temperature [ITS-90, deg C]
    'tv290C': 'temp_c',
    'c0S/m': 'cond_sm',         # Conductivity [S/m]
    'sal00': 'sal_psu',         # Salinity, Practical [PSU]
    'flECO-AFL': 'fluor_mgm3',  # Fluorescence, WET Labs ECO-AFL/FL [mg/m^3]
    'CStarAt0': 'cstar_atten_m',    # Beam attenuation, WET Labs C-Star [1/m]
    'CStarTr0': 'cstar_trans_pct',  # Beam transmission, WET Labs C-Star [%]
}
CNV_POS_TOL_DEG = 1e-4       # .cnv lat/lon must agree with the .hex position


def find_matching_cnv(hex_path, search_dirs):
    """Returns the .cnv for ``hex_path`` or None.

    ``search_dirs`` are tried in order.  A match is the same stem as the .hex
    file (any case), optionally followed by ``_<suffix>`` (e.g. 20250611_conv.cnv),
    so 20250416.cnv is never confused with 20250416b.cnv.
    """
    stem = Path(hex_path).stem.lower()
    for d in search_dirs:
        d = Path(d)
        if not d.is_dir():
            continue
        for cand in sorted(d.iterdir()):
            if not cand.is_file() or cand.suffix.lower() != '.cnv':
                continue
            c = cand.stem.lower()
            if c == stem or c.startswith(stem + '_'):
                return cand
    return None


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


def apply_cnv(df, cnv_path):
    """Adds .cnv columns (temp_c, cond_sm, sal_psu ...) to the per-scan frame.

    Rows are matched by the .cnv ``scan`` column (1-based).  Two numberings are
    tried -- the .hex line number (scan_index + 1) and the order among valid scans
    -- and the one whose .cnv latitude/longitude agrees with the .hex position
    is used, so truncated .hex lines cannot silently shift the match.  If neither
    agrees, nothing is merged.  Returns (df, info); ``info`` is for build metadata.
    """
    cnv_path = Path(cnv_path)
    cnv, meta = read_cnv(cnv_path)
    info = {
        'cnv_name': cnv_path.name,
        'cnv_sha256': hashlib.sha256(cnv_path.read_bytes()).hexdigest(),
        'datcnv_date': meta['datcnv_date'],
        'n_hex_scans': len(df), 'n_cnv_rows': len(cnv),
        'n_matched': 0, 'n_position_mismatch': 0, 'match_method': 'none',
        'status': 'not applied', 'columns': '',
    }
    if cnv.empty:
        info['status'] = 'no data rows in .cnv'
        return df, info

    scan_no = cnv['scan'].astype(int).to_numpy() if 'scan' in cnv.columns else np.arange(1, len(cnv) + 1)
    have_pos = {'latitude', 'longitude'} <= set(cnv.columns)
    valid_order = pd.Series(np.arange(1, len(df) + 1), index=df['scan_index'])

    best = None
    for method in ('scan_index', 'valid_order'):
        key = (df['scan_index'] + 1) if method == 'scan_index' else df['scan_index'].map(valid_order)
        lookup = pd.Series(np.arange(len(cnv)), index=scan_no)
        pos = key.map(lookup)
        matched = pos.notna().to_numpy()
        n_match = int(matched.sum())
        if n_match == 0:
            continue
        bad = 0
        if have_pos:
            ci = pos[matched].astype(int).to_numpy()
            dlat = np.abs(cnv['latitude'].to_numpy()[ci] - df['lat'].to_numpy()[matched])
            dlon = np.abs(cnv['longitude'].to_numpy()[ci] - df['lon'].to_numpy()[matched])
            bad = int(((dlat > CNV_POS_TOL_DEG) | (dlon > CNV_POS_TOL_DEG)).sum())
        cand = (bad, -n_match, method, pos)
        if best is None or cand[:2] < best[:2]:
            best = cand
        if bad == 0:
            break
    if best is None:
        info['status'] = 'no scans matched'
        return df, info
    bad, neg_match, method, pos = best
    n_match = -neg_match
    info.update(n_matched=n_match, n_position_mismatch=bad, match_method=method)
    if have_pos and bad > max(0, int(0.01 * n_match)):
        info['status'] = 'REJECTED: .cnv position does not match the .hex scans'
        return df, info

    matched = pos.notna().to_numpy()
    ci = pos[matched].astype(int).to_numpy()
    cols = []
    for short, col in CNV_COLUMNS.items():
        if short in cnv.columns and col not in cols:
            vals = np.full(len(df), np.nan)
            vals[matched] = cnv[short].to_numpy()[ci]
            df[col] = vals
            cols.append(col)
    info['columns'] = ','.join(cols)
    info['status'] = 'applied' if n_match >= len(df) else f'applied to {n_match} of {len(df)} scans'
    return df, info


# ---------------------------------------------------------------------------
# Core ingestion
# ---------------------------------------------------------------------------

def ingest_sbe21_hex(file_path, config, cruise_id=None, calibration=None, cnv_path=None):
    """Converts one flow-through .hex file to a per-scan DataFrame.

    Columns: cruise_id, file_key, scan_index, time_utc, lat, lon, depth_m,
    ch0_counts .. ch5_raw, aux_byte, flag_byte, speed_ms, qc_flag, plus
    station_name / operator from the header comments.  ``df.attrs`` carries
    header info and the malformed-line count.
    """
    file_path = Path(file_path)
    hdr = parse_hex_header(file_path)
    df = read_sbe21_hex(file_path, hdr['n_header_lines'])
    n_bad = len(df.attrs.get('bad_line_indices', []))
    if df.empty:
        raise ValueError(f"{file_path.name}: no valid scans found.")

    # --- time base -------------------------------------------------------
    source = config.get('TIME_SOURCE', 'nmea').lower()
    start = hdr['nmea_utc'] if source == 'nmea' else hdr['system_utc']
    if pd.isnull(start):
        start = hdr['system_utc'] if source == 'nmea' else hdr['nmea_utc']
        logging.warning("%s: requested time source missing; fell back.", file_path.name)
    if pd.isnull(start):
        raise ValueError(f"{file_path.name}: no usable start time in header.")
    interval = config.get('SAMPLE_INTERVAL_S', '')
    interval = float(interval) if str(interval).strip() else (hdr['sample_interval_s'] or 3.0)
    df['time_utc'] = start + pd.to_timedelta(df['scan_index'] * interval, unit='s')

    # --- position (hemisphere taken from the header) ----------------------
    lat_sign = -1.0 if (hdr['header_lat'] is not None and hdr['header_lat'] < 0) else 1.0
    lon_sign = -1.0 if (hdr['header_lon'] is None or hdr['header_lon'] < 0) else 1.0
    df['lat'] = lat_sign * df['lat_word'] / POS_SCALE
    df['lon'] = lon_sign * df['lon_word'] / POS_SCALE

    # --- depth (probable decimetres; 0 or absent = missing) ---------------
    depth = df['depth_word'].astype(float) / DEPTH_SCALE
    depth[(df['depth_word'] <= 0)] = np.nan
    df['depth_m'] = depth

    df = apply_qc_flags(df, config, hdr['header_lat'], hdr['header_lon'])
    df = apply_calibration(df, calibration)

    df.insert(0, 'file_key', file_path.stem)
    df.insert(0, 'cruise_id', cruise_id or '')
    df['station_name'] = hdr['station']
    df['operator'] = hdr['operator']
    df['sample_interval_s'] = interval

    keep = ['cruise_id', 'file_key', 'scan_index', 'time_utc', 'lat', 'lon', 'depth_m',
            'ch0_counts', 'ch1_counts', 'ch2_counts', 'ch3_raw', 'ch4_raw', 'ch5_raw',
            'aux_byte', 'flag_byte', 'speed_ms', 'qc_flag',
            'station_name', 'operator', 'sample_interval_s']
    out = df[keep].reset_index(drop=True)
    cnv_info = None
    if cnv_path is not None:
        out, cnv_info = apply_cnv(out, cnv_path)
        out = flag_implausible_tsc(out, config)
        out = flag_converted_spikes(out, config)
    out.attrs['header'] = hdr
    out.attrs['n_bad_lines'] = n_bad
    out.attrs['calibration'] = calibration
    out.attrs['cnv'] = cnv_info
    return out
