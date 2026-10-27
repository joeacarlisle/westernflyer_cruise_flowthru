"""Interactive dashboard for the Western Flyer flow-through (SBE 21) data.

Run:
    panel serve flowthru_holoviews.py --port 5007 --show
Requires processed/wf_flowthru.duckdb (run ``python main.py <cruise_id>`` first).

Mirrors ctd_holoviews.py in westernflyer_cruise_ctd (FastListTemplate, sidebar
widgets, tabs, DuckDB read-only connection, Tabulator tables).

Tabs
    Track Map      map on the left, stacked time-series panels (full cruise, shared
                   time axis) on the right.  Map: ship track coloured by the selected
                   variable, highlight of the window zoomed on
                   the time series, and an animated cruise timeline (play / pause /
                   scrub, speed, trail length, skip-gaps option).  A red cursor on
                   every time-series panel follows the animation.
    Multi-Axis Profile
                   temperature, salinity and fluorescence on one shared time axis, each on its
                   own colour-matched y-axis (clock time or elapsed minutes).  Beam transmission is
                   not plotted: it is not in the database.
    File Summary   one row per logging session
    Data Table     paginated scans with CSV export

Converted variables (temp_c, cond_sm, sal_psu, fluor_mgm3, and C-Star beam attenuation /
transmission when present) come from SBE Data Processing .cnv files imported by main.py.
Cruises without a .cnv show only speed and depth, and a warning appears in the sidebar.
Set WF_DEFAULT_CRUISE to choose the cruise shown at start-up.
"""

import math
import os
import pathlib
from functools import lru_cache
from io import BytesIO

import cartopy.crs as ccrs
import duckdb
import geoviews as gv
import geoviews.tile_sources as gvts
import holoviews as hv
import numpy as np
import pandas as pd
import panel as pn
from bokeh.models import CustomJS

# 1. ENGINE INITIALIZATION
pn.extension('tabulator')
hv.extension('bokeh')
gv.extension('bokeh')

# BokehJS paints the white backing of a multi-plot (grid) export without the browser's display-scaling
# factor, so at 125% Windows scaling only the top-left 80% of the PNG is filled and the toolbar corner
# exports transparent.  This one-time patch makes grid exports paint white behind everything.  (No view
# here is a grid today, since every chart is its own pane, but it keeps any future hv.Layout export clean.)
_WHITE_EXPORT_JS = """
const M = Bokeh.Models.get('GridPlot');
const V = M && M.prototype.default_view;
if (V && !V.prototype.__white_export) {
  V.prototype.__white_export = true;
  const orig = V.prototype.export;
  V.prototype.export = function (...args) {
    const out = orig.apply(this, args);
    try {
      const c = out.canvas, ctx = out.ctx;
      if (c instanceof HTMLCanvasElement) {
        ctx.save(); ctx.setTransform(1, 0, 0, 1, 0, 0);
        ctx.globalCompositeOperation = 'destination-over';
        ctx.fillStyle = 'white'; ctx.fillRect(0, 0, c.width, c.height); ctx.restore();
      }
    } catch (e) {}
    return out;
  };
}
"""
if pn.state.curdoc is not None:
    pn.state.curdoc.js_on_event('document_ready', CustomJS(code=_WHITE_EXPORT_JS))

# 2. DATABASE CONNECTIONS
BASE_DIR = pathlib.Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "processed" / "wf_flowthru.duckdb"
TABLE_NAME = "flowthru_data"

MAP_WIDTH = 700               # map (left) and time-series (right) share one tab
TS_WIDTH = 620
TS_PANEL_HEIGHT = 170
MAX_POINTS = 30000            # decimate plotted scans to keep the browser responsive
# Esri Ocean has no tiles at close zoom (the map shows 'Map data not yet available').  When the
# plotted track spans less than this many degrees, use Esri satellite imagery, which has close-up tiles.
CLOSEUP_SPAN_DEG = 1.0
QC_BAD_MASK = 19              # qc_flag bits 1 (spike), 2 (speed) and 16 (implausible T/S)

# The database stores UTC.  Everything the dashboard shows (time slider, time axes, animation clock, tables)
# is local ship time; queries convert back to UTC.  Set WF_DISPLAY_TZ=UTC to show UTC again.  (Local times
# repeat for one hour when clocks go back in autumn; data logged across that change would overlap.)
DISPLAY_TZ = os.environ.get("WF_DISPLAY_TZ", "America/Los_Angeles")


def _to_local(series):
    """Naive-UTC datetime Series -> naive local ship time."""
    return series.dt.tz_localize('UTC').dt.tz_convert(DISPLAY_TZ).dt.tz_localize(None)


def _utc_to_local_dt(ts):
    """Naive-UTC timestamp -> naive local python datetime (for the time slider)."""
    return pd.Timestamp(ts).tz_localize('UTC').tz_convert(DISPLAY_TZ).tz_localize(None).to_pydatetime()


def _local_to_utc(ts):
    """Naive local timestamp (from the time slider) -> naive UTC, for database queries."""
    return pd.Timestamp(ts).tz_localize(DISPLAY_TZ, ambiguous=False, nonexistent='shift_forward') \
        .tz_convert('UTC').tz_localize(None)


def _tz_abbr(ts):
    """Zone abbreviation (PDT / PST / UTC) in force at a naive local timestamp."""
    return pd.Timestamp(ts).tz_localize(DISPLAY_TZ, ambiguous=False, nonexistent='shift_forward').tzname()

if not DB_PATH.exists():
    raise FileNotFoundError(f"Database not found at {DB_PATH}. Run main.py first.")

con = duckdb.connect(str(DB_PATH), read_only=True)

_table_cols = set(con.execute(f"SELECT * FROM {TABLE_NAME} LIMIT 0").df().columns)

# Variables offered for map colour / time series: (label -> (column, unit note)).
_VAR_CANDIDATES = [
    ("Temperature (°C)", "temp_c"), ("Conductivity (S/m)", "cond_sm"),
    ("Salinity (PSU)", "sal_psu"),
    ("Fluorescence (mg/m³)", "fluor_mgm3"),
    ("Beam attenuation (1/m)", "cstar_atten_m"), ("Beam transmission (%)", "cstar_trans_pct"),
    ("Ship speed (m/s)", "speed_ms"), ("Depth (m)", "depth_m"),
]
VARS = {label: col for label, col in _VAR_CANDIDATES if col in _table_cols}
CALIBRATED = "temp_c" in _table_cols

# 3. GLOBAL WIDGETS
raw_cruises = con.execute(f"SELECT DISTINCT cruise_id FROM {TABLE_NAME} ORDER BY 1").df()['cruise_id'].tolist()
# 2025_Baja is listed first and is the cruise shown at start-up (WF_DEFAULT_CRUISE overrides the start-up cruise).
FIRST_CRUISE = "2025_Baja"
if FIRST_CRUISE in raw_cruises:
    raw_cruises = [FIRST_CRUISE] + [c for c in raw_cruises if c != FIRST_CRUISE]
default_cruise = os.environ.get("WF_DEFAULT_CRUISE", "")
if default_cruise not in raw_cruises:
    default_cruise = raw_cruises[0]


def _files_for(cruise):
    return ["All files"] + con.execute(
        f"SELECT file_key FROM {TABLE_NAME} WHERE cruise_id = ? GROUP BY file_key ORDER BY MIN(time_utc)",
        (cruise,)).df()['file_key'].tolist()


def _time_bounds(cruise):
    lo, hi = con.execute(f"SELECT MIN(time_utc), MAX(time_utc) FROM {TABLE_NAME} WHERE cruise_id = ?",
                         (cruise,)).fetchone()
    return _utc_to_local_dt(lo), _utc_to_local_dt(hi)


_lo, _hi = _time_bounds(default_cruise)
cruise_select = pn.widgets.Select(name='Cruise ID', options=raw_cruises, value=default_cruise)
file_select = pn.widgets.Select(name='File / leg', options=_files_for(default_cruise), value="All files")
var_select = pn.widgets.Select(name='Colour map by', options=list(VARS),
                               value=list(VARS)[0])
time_slider = pn.widgets.DatetimeRangeSlider(name='Time range (local time)', start=_lo, end=_hi, value=(_lo, _hi))
qc_checkbox = pn.widgets.Checkbox(name='Hide QC-flagged scans (spike / speed / bad T-S)', value=False)

# Animation controls (shown in the Track Map tab).
MAX_TRAIL_POINTS = 4000
animate_toggle = pn.widgets.Checkbox(name='Animate timeline', value=True)
player = pn.widgets.Player(name='Cruise timeline', start=0, end=0, value=0, step=1, interval=100,
                           loop_policy='once', show_loop_controls=False, width=420)
speed_select = pn.widgets.Select(name='Speed', width=120,
                                 options={'2 frames/s': 500, '5 frames/s': 200,
                                          '10 frames/s': 100, '20 frames/s': 50}, value=100)
step_select = pn.widgets.Select(name='Minutes per frame', options=[5, 10, 30, 60], value=5, width=130)
tail_select = pn.widgets.Select(name='Trail', width=150,
                                options={'Whole cruise so far': 0, 'Last 6 hours': 6,
                                         'Last 24 hours': 24, 'Last 3 days': 72}, value=0)
skip_checkbox = pn.widgets.Checkbox(name='Skip gaps (jump to next data)', value=True)
# The Player's built-in Once/Loop/Reflect radios do not keep their selection, so use a normal selector.
loop_select = pn.widgets.RadioButtonGroup(name='At the end', options={'Once': 'once', 'Loop': 'loop',
                                          'Reflect': 'reflect'}, value='once', button_type='default',
                                          button_style='outline', width=200)
loop_select.link(player, value='loop_policy')
anim_controls = pn.Row(player, pn.Column(pn.pane.Markdown('At the end', margin=(0, 5, 0, 5)), loop_select),
                       speed_select, step_select, tail_select, skip_checkbox, align='end')
animate_toggle.param.watch(lambda e: setattr(anim_controls, 'visible', e.new), 'value')
speed_select.param.watch(lambda e: setattr(player, 'interval', e.new), 'value')

def _notice_impl(cruise):
    """Warn when the selected cruise has no converted (.cnv) temperature / salinity."""
    n = con.execute(f"SELECT COUNT(temp_c) FROM {TABLE_NAME} WHERE cruise_id = ?", (cruise,)).fetchone()[0] \
        if CALIBRATED else 0
    return pn.pane.Alert(
        "No converted values for this cruise (no SBE Data Processing .cnv imported). "
        "Temperature, conductivity and salinity are not available.",
        alert_type='warning', visible=(n == 0))


notice = pn.bind(_notice_impl, cruise_select)


def _on_cruise(event):
    file_select.options = _files_for(event.new)
    file_select.value = "All files"
    lo, hi = _time_bounds(event.new)
    time_slider.start, time_slider.end, time_slider.value = lo, hi, (lo, hi)


def _on_file(event):
    where, params = "cruise_id = ?", [cruise_select.value]
    if event.new != "All files":
        where += " AND file_key = ?"
        params.append(event.new)
    lo, hi = con.execute(f"SELECT MIN(time_utc), MAX(time_utc) FROM {TABLE_NAME} WHERE {where}", params).fetchone()
    lo, hi = _utc_to_local_dt(lo), _utc_to_local_dt(hi)
    time_slider.start, time_slider.end, time_slider.value = lo, hi, (lo, hi)


cruise_select.param.watch(_on_cruise, 'value')
file_select.param.watch(_on_file, 'value')

# 4. DATA LOGIC
_WIDGETS = (cruise_select, file_select, time_slider, qc_checkbox)


def _query(cruise, file_key, t_range, hide_qc):
    sql = f"SELECT * FROM {TABLE_NAME} WHERE cruise_id = ? AND time_utc BETWEEN ? AND ?"
    params = [cruise, _local_to_utc(t_range[0]), _local_to_utc(t_range[1])]
    if file_key and file_key != "All files":
        sql += " AND file_key = ?"
        params.append(file_key)
    if hide_qc:
        sql += f" AND (qc_flag & {QC_BAD_MASK}) = 0"
    sql += " ORDER BY time_utc"
    df = con.execute(sql, params).df()
    # time_utc stays as stored (UTC, used for export); time_local is what every chart and table shows.
    df.insert(df.columns.get_loc('time_utc') + 1, 'time_local', _to_local(df['time_utc']))
    return df


def _decimate(df, n=MAX_POINTS):
    if len(df) <= n:
        return df
    return df.iloc[::math.ceil(len(df) / n)]


def get_df(cruise, file_key, t_range, hide_qc):
    return _query(cruise, file_key, t_range, hide_qc)


_HIDDEN_COLS = ["ch0_counts", "ch1_counts", "ch2_counts"]   # raw counts stay in the database only


def _shown(df):
    return df.drop(columns=[c for c in _HIDDEN_COLS if c in df.columns])


def download_csv():
    df = _shown(get_df(cruise_select.value, file_select.value, time_slider.value, qc_checkbox.value))
    sio = BytesIO()
    df.to_csv(sio, index=False)
    sio.seek(0)
    return sio


csv_button = pn.widgets.FileDownload(callback=download_csv, filename='Flowthru_Export.csv', label='Export CSV',
                                     button_type='primary', sizing_mode='stretch_width')

# Zoom on the time-series tab -> highlighted window on the map tab.
range_x = hv.streams.RangeX()


def _to_ts(v):
    if v is None:
        return None
    if isinstance(v, (int, float, np.integer, np.floating)):
        return pd.to_datetime(v, unit='ms')
    return pd.to_datetime(v)


# Animation timeline ---------------------------------------------------------
@lru_cache(maxsize=4)
def _timeline(cruise, file_key, t0, t1, hide_qc, step_min, skip_gaps):
    """Scans for the current filters plus the frame end-times (unit-independent)."""
    df = get_df(cruise, file_key, (t0, t1), hide_qc).dropna(subset=['lat', 'lon']).reset_index(drop=True)
    if df.empty:
        return df, pd.DatetimeIndex([])
    step = pd.Timedelta(minutes=step_min)
    bins = df['time_local'].dt.floor(f'{step_min}min')
    if skip_gaps:
        starts = pd.DatetimeIndex(np.sort(bins.unique()))
    else:
        starts = pd.date_range(bins.min(), bins.max(), freq=f'{step_min}min')
    # Frame 1 is the first scan alone (the red dot sits at the start of the track); later frames
    # are the end of each time bin.
    ends = pd.DatetimeIndex([df['time_local'].iloc[0]]).append(starts + step)
    return df, ends.drop_duplicates()


def _frame_state(frame, cruise, file_key, t_range, hide_qc, step_min, skip_gaps, tail_h):
    df, ends = _timeline(cruise, file_key, t_range[0], t_range[1], hide_qc, step_min, skip_gaps)
    if len(ends) == 0:
        return None
    k = int(min(max(frame or 0, 0), len(ends) - 1))
    t = ends[k]
    hi = int(df['time_local'].searchsorted(t, side='right'))
    lo = 0 if not tail_h else int(df['time_local'].searchsorted(t - pd.Timedelta(hours=tail_h), side='left'))
    return df, k, t, lo, hi, len(ends)


def _sync_player(*_events):
    _, ends = _timeline(cruise_select.value, file_select.value, time_slider.value[0], time_slider.value[1],
                        qc_checkbox.value, step_select.value, skip_checkbox.value)
    player.start, player.end = 0, max(len(ends) - 1, 0)
    player.value = 0


for _w in (cruise_select, file_select, time_slider, qc_checkbox, step_select, skip_checkbox):
    _w.param.watch(_sync_player, 'value')
_sync_player()


def _readout(frame, cruise, file_key, t_range, hide_qc, step_min, skip_gaps, tail_h, var_label, animate):
    if not animate:
        return ""
    st = _frame_state(frame, cruise, file_key, t_range, hide_qc, step_min, skip_gaps, tail_h)
    if st is None or st[4] == 0:
        return "No data for the current selection."
    df, k, t, lo, hi, n = st
    last = df.iloc[hi - 1]
    val = last.get(VARS[var_label])
    val_txt = "n/a" if val is None or pd.isna(val) else f"{val:,.1f}"
    spd = last.get('speed_ms')
    spd_txt = "n/a" if spd is None or pd.isna(spd) else f"{spd:.1f} m/s"
    idle = " &nbsp;<em>(no data in this interval)</em>" if t - last['time_local'] > pd.Timedelta(minutes=2 * step_min) else ""
    return (f"**{t:%Y-%m-%d %H:%M} {_tz_abbr(t)}** &nbsp;|&nbsp; frame {k + 1} / {n} &nbsp;|&nbsp; "
            f"{last['lat']:.3f}, {last['lon']:.3f} &nbsp;|&nbsp; speed {spd_txt} &nbsp;|&nbsp; "
            f"{var_label}: {val_txt}{idle}")


def _basemap_for(dfp):
    """Esri Ocean for wide views; Esri imagery for a track small enough to need close-up tiles."""
    if dfp.empty:
        return gvts.EsriOceanBase * gvts.EsriOceanReference
    span = max(float(dfp['lat'].max() - dfp['lat'].min()), float(dfp['lon'].max() - dfp['lon'].min()))
    if span < CLOSEUP_SPAN_DEG:
        return gvts.EsriImagery
    return gvts.EsriOceanBase * gvts.EsriOceanReference


# 5. TAB FUNCTIONS
# Expand / collapse the track map (state only; never displayed).
map_big = pn.widgets.Checkbox(value=False)
MAP_BIG_WIDTH = MAP_WIDTH + TS_WIDTH
MAP_BIG_HEIGHT = 760
expand_map_button = pn.widgets.Button(name='\u2922 Expand map', width=120, height=28, button_type='light')
expand_map_button.on_click(lambda _e: setattr(map_big, 'value', True))
collapse_map_button = pn.widgets.Button(name='\u2921 Collapse map', width=130, height=28, button_type='primary')
collapse_map_button.on_click(lambda _e: setattr(map_big, 'value', False))


@pn.depends(cruise_select, file_select, time_slider, qc_checkbox, var_select,
            animate_toggle, step_select, skip_checkbox, tail_select, map_big)
def view_map(cruise, file_key, t_range, hide_qc, var_label, animate, step_min, skip_gaps, tail_h,
             big=False):
    map_w = MAP_BIG_WIDTH if big else MAP_WIDTH
    df = get_df(cruise, file_key, t_range, hide_qc)
    if df.empty:
        return pn.pane.Alert("No data for the current selection.")
    col = VARS[var_label]
    dfp = _decimate(df)[['lon', 'lat', col, 'time_local', 'file_key']].dropna(subset=['lon', 'lat'])
    vals = dfp[col].dropna()
    clim = (float(vals.quantile(0.02)), float(vals.quantile(0.98))) if len(vals) else (0, 1)
    tiles = _basemap_for(dfp)

    if animate:
        context = gv.Points(dfp[['lon', 'lat']], ['lon', 'lat'], crs=ccrs.PlateCarree()).opts(
            size=2, color='#7f8c8d', alpha=0.3)
        vd = [col, 'time_local']
        empty = dfp.iloc[0:0]

        def frame_layers(frame):
            st = _frame_state(frame, cruise, file_key, t_range, hide_qc, step_min, skip_gaps, tail_h)
            if st is None or st[4] == 0:
                trail, last = df.iloc[0:0], df.iloc[0:0]
            else:
                d, k, t, lo, hi, n = st
                seg = d.iloc[lo:hi]
                trail = _decimate(seg, MAX_TRAIL_POINTS)
                last = d.iloc[hi - 1:hi]
            trail_pts = gv.Points(trail[['lon', 'lat', col, 'time_local']], ['lon', 'lat'], vdims=vd,
                                  crs=ccrs.PlateCarree()).opts(
                color=col, cmap='Viridis', clim=clim, colorbar=True, size=5, alpha=0.9, tools=['hover'],
                colorbar_opts={'title': var_label})
            marker = gv.Points(last[['lon', 'lat']], ['lon', 'lat'], crs=ccrs.PlateCarree()).opts(
                size=16, color='red', marker='circle', line_color='white', line_width=2)
            return trail_pts * marker

        stream = hv.streams.Params(player, ['value'], rename={'value': 'frame'})
        layers = tiles * context * hv.DynamicMap(frame_layers, streams=[stream])
        return layers.opts(width=map_w, height=MAP_BIG_HEIGHT if big else 620, title=f"Cruise timeline - {var_label}",
                           hooks=[_white_background])

    track = gv.Points(dfp, ['lon', 'lat'], vdims=[col, 'time_local', 'file_key'], crs=ccrs.PlateCarree()).opts(
        color=col, cmap='Viridis', clim=clim, colorbar=True, size=4, alpha=0.8, tools=['hover'],
        colorbar_opts={'title': var_label})
    layers = tiles * track

    full = df.dropna(subset=['lon', 'lat'])

    def highlight(x_range):
        if x_range is None or full.empty:
            sel = full.iloc[0:0]
        else:
            lo, hi = _to_ts(x_range[0]), _to_ts(x_range[1])
            sel = _decimate(full[(full['time_local'] >= lo) & (full['time_local'] <= hi)])
        return gv.Points(sel[['lon', 'lat']], ['lon', 'lat'], crs=ccrs.PlateCarree()).opts(
            color='red', size=6, alpha=0.9)

    layers = layers * hv.DynamicMap(highlight, streams=[range_x])
    return layers.opts(width=map_w, height=MAP_BIG_HEIGHT if big else 650, title=f"Track - {var_label}",
                       hooks=[_white_background])


readout = pn.pane.Markdown(pn.bind(_readout, player.param.value, cruise_select, file_select, time_slider,
                                   qc_checkbox, step_select, skip_checkbox, tail_select, var_select,
                                   animate_toggle), margin=(0, 10))


_CONVERTED = {"temp_c", "sal_psu", "cond_sm", "fluor_mgm3", "cstar_atten_m", "cstar_trans_pct"}
# One colour per variable, shared by the Track Map time series and the Multi-Axis Profile:
# temperature blue, salinity red, fluorescence green.
VAR_COLOURS = {"temp_c": '#1f4fd8', "sal_psu": '#d62728', "fluor_mgm3": '#2ca02c'}
_TS_PANELS = [("temp_c", "Temperature (°C)", VAR_COLOURS["temp_c"]),
              ("sal_psu", "Salinity (PSU)", VAR_COLOURS["sal_psu"]),
              ("cond_sm", "Conductivity (S/m)", '#5aa6a0'),
              ("fluor_mgm3", "Fluorescence (mg/m³)", VAR_COLOURS["fluor_mgm3"]),
              ("cstar_atten_m", "Beam attenuation (1/m)", '#8a6d3b'),
              ("cstar_trans_pct", "Beam transmission (%)", '#7f8c8d'),
              ("speed_ms", "Ship speed (m/s)", '#5aa6a0'), ("depth_m", "Depth (m)", '#7f8c8d')]


def _white_background(plot, element):
    """Opaque white figure with dark labels, so exported PNGs have no transparent (checkerboard) area."""
    fig = plot.state
    fig.background_fill_color = 'white'
    fig.border_fill_color = 'white'
    # The dashboard theme sets border_fill_alpha = 0 (transparent).  Bokeh does not send a property that
    # equals its default (1.0), so the theme would win; 0.999 is sent and still exports fully opaque.
    fig.background_fill_alpha = 0.999
    fig.border_fill_alpha = 0.999
    fig.outline_line_color = '#888888'
    if fig.title is not None:
        fig.title.text_color = '#222222'
    for ax in list(fig.xaxis) + list(fig.yaxis):
        ax.axis_label_text_color = '#222222'
        ax.major_label_text_color = '#222222'


# Expand / collapse a single chart.  expanded_ts holds the label of the expanded chart ('' = none).
expanded_ts = pn.widgets.TextInput(value='')
EXPANDED_WIDTH = MAP_WIDTH + TS_WIDTH
EXPANDED_HEIGHT = 560


def _make_expand_button(label):
    b = pn.widgets.Button(name='\u2922 Expand', width=90, height=26, margin=(0, 0, 0, 5), button_type='light')
    b.on_click(lambda _e, lab=label: setattr(expanded_ts, 'value', lab))
    return b


_expand_buttons = {label: _make_expand_button(label) for _c, label, _k in _TS_PANELS}
collapse_button = pn.widgets.Button(name='\u2921 Collapse', width=100, height=28, button_type='primary')
collapse_button.on_click(lambda _e: setattr(expanded_ts, 'value', ''))


@pn.depends(cruise_select, file_select, time_slider, qc_checkbox, animate_toggle, step_select, skip_checkbox,
            tail_select, expanded_ts)
def view_timeseries(cruise, file_key, t_range, hide_qc, animate, step_min, skip_gaps, tail_h, expanded):
    df = get_df(cruise, file_key, t_range, hide_qc)
    if df.empty:
        return pn.pane.Alert("No data for the current selection.")
    full = df
    dfp = _decimate(df)
    curves, first = [], None
    if animate:
        stream = hv.streams.Params(player, ['value'], rename={'value': 'frame'})

        def cursor(frame):
            st = _frame_state(frame, cruise, file_key, t_range, hide_qc, step_min, skip_gaps, tail_h)
            t = st[2] if st is not None else dfp['time_local'].iloc[0]
            return hv.VLine(t).opts(color='red', line_width=1.5)
    if expanded and not any(l == expanded and c in dfp.columns and dfp[c].notna().sum() > 0
                            for c, l, _k in _TS_PANELS):
        expanded = ''                      # expanded chart has no data in this selection: show all again
    for col, label, color in _TS_PANELS:
        if col not in dfp.columns or dfp[col].notna().sum() == 0:
            continue
        if expanded and label != expanded:
            continue
        big = bool(expanded)
        c = hv.Curve(dfp, 'time_local', col, label=label).opts(
            color=color, height=EXPANDED_HEIGHT if big else TS_PANEL_HEIGHT,
            width=EXPANDED_WIDTH if big else TS_WIDTH, tools=['hover'], ylabel=label,
            fontsize={'ylabel': 12} if big else {'ylabel': 9}, xlabel='', title='', show_legend=False,
            line_width=1.3 if big else 1, bgcolor='white', hooks=[_white_background])
        if first is None:
            first = c                      # bare curve: source for the zoom -> map highlight link
        if col in _CONVERTED and not hide_qc:
            flagged = full[(full['qc_flag'] & QC_BAD_MASK) > 0]
            if not flagged.empty:
                c = c * hv.Scatter(_decimate(flagged), 'time_local', col).opts(color='#222222', size=4, alpha=0.7)
        if animate:
            c = c * hv.DynamicMap(cursor, streams=[stream])
        curves.append(c)
    if not curves:
        return pn.pane.Alert("No plottable variables.")
    range_x.source = first
    # One HoloViews pane per panel (panes share the time axis through Panel's linked axes).  A single
    # hv.Layout renders with a zero-size plot frame when it sits beside the map inside pn.Tabs.
    if expanded:
        return pn.Column(collapse_button, pn.pane.HoloViews(curves[0], linked_axes=True, sizing_mode='fixed'),
                         width=EXPANDED_WIDTH + 20, margin=0)
    items = []
    for (col, label, _k), c in zip([t for t in _TS_PANELS
                                    if t[0] in dfp.columns and dfp[t[0]].notna().sum() > 0], curves):
        items += [_expand_buttons[label], pn.pane.HoloViews(c, linked_axes=True, sizing_mode='fixed')]
    return pn.Column(*items, width=TS_WIDTH + 20, margin=0)


# Track Map tab: map on the left, stacked time series (full cruise, moving red cursor) on the right.
# Both views listen to the same player, so the map marker and every cursor advance together.
def _map_tab_body(expanded, map_is_big):
    # Normal: map left, charts right.  A chart expanded: that chart alone, full width (the map is hidden).
    # Map expanded: the map alone, full width (the charts are hidden).
    if map_is_big:
        return pn.Column(collapse_map_button, view_map)
    if expanded:
        return pn.Column(view_timeseries)
    return pn.Row(pn.Column(expand_map_button, view_map), view_timeseries)


view_map_tab = pn.Column(pn.Row(animate_toggle, anim_controls), readout,
                         pn.bind(_map_tab_body, expanded_ts, map_big))


# Multi-Axis Profile tab: temperature, salinity and fluorescence overlaid on one time axis, each on its
# own colour-matched y-axis fitted to that variable's data (the layout of the SBE Data Processing
# flow-through plot).  Beam transmission is not offered because the database has no transmission data.
# Built directly with Bokeh (one explicit Range1d per axis), so every axis keeps its own fitted scale.
# (column, label, colour, side of the plot).  Temperature sits next to the plot on the left,
# fluorescence outside it, salinity on the right.
_MULTI_AXIS_VARS = [("temp_c", "Temperature (°C)", VAR_COLOURS["temp_c"], 'left'),
                    ("sal_psu", "Salinity (PSU)", VAR_COLOURS["sal_psu"], 'right'),
                    ("fluor_mgm3", "Fluorescence (mg/m³)", VAR_COLOURS["fluor_mgm3"], 'left')]
_MULTI_AXIS_LABELS = [lab for col, lab, _k, _s in _MULTI_AXIS_VARS if col in _table_cols]
MULTI_AXIS_WIDTH = MAP_WIDTH + TS_WIDTH
MULTI_AXIS_HEIGHT = 560
MULTI_AXIS_GAP_MIN = 30        # break the lines where logging stops for longer than this
MULTI_AXIS_PAD = 0.04          # head-room added to each axis, as a fraction of its data range

ma_time_axis = pn.widgets.RadioButtonGroup(name='Time axis', options={'Clock time': 'clock', 'Elapsed minutes': 'elapsed'},
                                           value='clock', button_type='default', button_style='outline')
ma_fit = pn.widgets.RadioButtonGroup(name='Fit axes to', options={'Full range': 'full', 'Ignore spikes': 'robust'},
                                     value='full', button_type='default', button_style='outline')
ma_var_select = pn.widgets.CheckBoxGroup(name='Variables', options=_MULTI_AXIS_LABELS,
                                         value=list(_MULTI_AXIS_LABELS), inline=True)


def _fit_range(values, robust):
    """(low, high) y-limits that fit one variable: its full range, or 1st-99th percentile if robust."""
    v = values.dropna()
    lo, hi = (float(v.quantile(0.01)), float(v.quantile(0.99))) if robust else (float(v.min()), float(v.max()))
    pad = (hi - lo) * MULTI_AXIS_PAD or max(abs(hi) * 0.01, 0.1)
    return lo - pad, hi + pad


def _multi_axis_figure(dfp, cols, xcol, xlabel, limits):
    """One Bokeh figure; each variable is drawn against its own fixed Range1d and colour-matched axis."""
    from bokeh.models import ColumnDataSource, HoverTool, LinearAxis, Range1d
    from bokeh.plotting import figure

    fig = figure(width=MULTI_AXIS_WIDTH, height=MULTI_AXIS_HEIGHT, x_axis_type='datetime' if xcol == 'time_local' else 'linear',
                 tools='pan,wheel_zoom,box_zoom,reset,save', active_scroll='wheel_zoom', toolbar_location='right',
                 x_axis_label=xlabel, background_fill_color='white', border_fill_color='white')
    fig.background_fill_alpha = fig.border_fill_alpha = 0.999     # opaque white in exported PNGs
    fig.xaxis.axis_label_text_color = fig.xaxis.major_label_text_color = '#222222'
    fig.xgrid.grid_line_alpha = fig.ygrid.grid_line_alpha = 0.0   # no grid: several scales share the plot
    fig.outline_line_color = '#888888'
    for i, (col, label, colour, side) in enumerate(cols):
        lo, hi = limits[col]
        if i == 0:                                    # first variable owns the default axis
            fig.y_range = Range1d(lo, hi)
            axis, range_name = fig.yaxis[0], 'default'
            fig.yaxis[0].axis_label = label
            if side == 'right':
                fig.yaxis[0].visible = False
                axis = LinearAxis(axis_label=label)
                fig.add_layout(axis, 'right')
        else:
            range_name = col
            fig.extra_y_ranges[range_name] = Range1d(lo, hi)
            axis = LinearAxis(y_range_name=range_name, axis_label=label)
            fig.add_layout(axis, side)
        for prop in ('axis_label_text_color', 'major_label_text_color', 'major_tick_line_color', 'axis_line_color'):
            setattr(axis, prop, colour)
        src = ColumnDataSource({'x': dfp[xcol], 'y': dfp[col]})
        glyph = fig.line('x', 'y', source=src, color=colour, line_width=1.3, legend_label=label,
                         y_range_name=range_name, muted_alpha=0.1)
        tips = [(label, '@y{0.000}'), ('Time', '@x{%F %T}' if xcol == 'time_local' else '@x{0.00} min')]
        fig.add_tools(HoverTool(renderers=[glyph], tooltips=tips,
                                formatters={'@x': 'datetime'} if xcol == 'time_local' else {}))
    fig.legend.click_policy = 'mute'
    fig.legend.orientation = 'horizontal'
    fig.add_layout(fig.legend[0], 'above')
    return fig


@pn.depends(cruise_select, file_select, time_slider, qc_checkbox, ma_time_axis, ma_fit, ma_var_select)
def view_multi_axis(cruise, file_key, t_range, hide_qc, x_mode, fit, chosen):
    df = get_df(cruise, file_key, t_range, hide_qc)
    if df.empty:
        return pn.pane.Alert("No data for the current selection.")
    dfp = _decimate(df).copy()
    cols = [(c, lab, colour, side) for c, lab, colour, side in _MULTI_AXIS_VARS
            if lab in chosen and c in dfp.columns and dfp[c].notna().any()]
    if not cols:
        return pn.pane.Alert("No converted values to plot for this selection (select at least one variable; "
                             "cruises without a .cnv have no temperature, salinity or fluorescence).",
                             alert_type='warning')
    limits = {c: _fit_range(dfp[c], fit == 'robust') for c, _l, _k, _s in cols}
    if x_mode == 'elapsed':
        xcol, xlabel, eps = 'elapsed_min', 'Time, Elapsed [minutes]', 1 / 60
        dfp[xcol] = (dfp['time_local'] - dfp['time_local'].iloc[0]).dt.total_seconds() / 60
    else:
        xcol, xlabel, eps = 'time_local', 'Time (local)', pd.Timedelta(seconds=1)
    gaps = dfp['time_local'].diff() > pd.Timedelta(minutes=MULTI_AXIS_GAP_MIN)
    if gaps.any():                          # a NaN row before each gap stops the line bridging it
        brk = dfp[gaps].copy()
        brk[[c for c, _l, _k, _s in cols]] = np.nan
        brk[xcol] = brk[xcol] - eps
        dfp = pd.concat([dfp, brk]).sort_values(xcol)
    fig = _multi_axis_figure(dfp, cols, xcol, xlabel, limits)
    return pn.pane.Bokeh(fig, sizing_mode='fixed')


view_multi_axis_tab = pn.Column(
    pn.Row(pn.pane.Markdown('**Time axis**', margin=(5, 5, 0, 5)), ma_time_axis,
           pn.pane.Markdown('**Fit axes to**', margin=(5, 5, 0, 25)), ma_fit,
           pn.pane.Markdown('**Variables**', margin=(5, 5, 0, 25)), ma_var_select),
    view_multi_axis)


_SUMMARY_EXTRA = [("temp_c", "Median temp (°C)", 2), ("sal_psu", "Median salinity (PSU)", 2),
                  ("fluor_mgm3", "Median fluor. (mg/m³)", 2)]


def _file_summary_impl(target_cruise):
    # Medians of the converted variables, ignoring QC-flagged scans (NULL if no .cnv).
    extra = "".join(
        f',\n        ROUND(MEDIAN(CASE WHEN (qc_flag & {QC_BAD_MASK}) = 0 THEN {col} END), {nd}) AS "{label}"'
        for col, label, nd in _SUMMARY_EXTRA if col in _table_cols)
    query = f"""
        SELECT file_key AS "File", station_name AS "Leg / station", operator AS "Operator",
        MIN(time_utc)::TIMESTAMP AS "Start (UTC)", MAX(time_utc)::TIMESTAMP AS "End (UTC)",
        COUNT(*) AS "Scans",
        ROUND(100.0 * SUM(CASE WHEN (qc_flag & {QC_BAD_MASK}) = 0 THEN 1 ELSE 0 END) / COUNT(*), 1) AS "Health %",
        ROUND(100.0 * COUNT(depth_m) / COUNT(*), 0) AS "Depth %",
        ROUND(MEDIAN(speed_ms), 1) AS "Median speed (m/s)"{extra}
        FROM {TABLE_NAME} WHERE cruise_id = ?
        GROUP BY file_key, station_name, operator ORDER BY "Start (UTC)"
    """
    df_sum = con.execute(query, (target_cruise,)).df()
    for col in ("Start (UTC)", "End (UTC)"):
        df_sum[col] = _to_local(df_sum[col])
    df_sum = df_sum.rename(columns={"Start (UTC)": "Start (local)", "End (UTC)": "End (local)"})
    config = {'columns': [{'field': 'Health %', 'formatter': 'progress',
                           'formatterParams': {'color': '#28a745', 'legend': True}}]}
    table = pn.widgets.Tabulator(df_sum, theme='midnight', show_index=False, sizing_mode='stretch_both',
                                 configuration=config)
    return pn.Column("# File Summary", table, sizing_mode='stretch_both')


view_file_summary = pn.Column(pn.bind(_file_summary_impl, cruise_select), sizing_mode='stretch_both')


def _tabular_impl(cruise, file_key, t_range, hide_qc):
    df = get_df(cruise, file_key, t_range, hide_qc)
    if df.empty:
        return pn.pane.Alert("No data for the current selection.")
    return pn.widgets.Tabulator(_shown(df), pagination='remote', page_size=15, theme='midnight',
                                sizing_mode='stretch_both')


view_tabular_data = pn.Column(
    pn.bind(_tabular_impl, cruise_select, file_select, time_slider, qc_checkbox),
    sizing_mode='stretch_both')

# 6. ASSEMBLY
tabs = pn.Tabs(
    ("Track Map", view_map_tab),
    ("Multi-Axis Profile", view_multi_axis_tab),
    ("File Summary", view_file_summary),
    ("Data Table", view_tabular_data),
    dynamic=True, active=0)

dashboard = pn.template.FastListTemplate(
    title="Western Flyer - Flow-through (SBE 21)",
    sidebar=[notice, cruise_select, file_select, var_select, time_slider, qc_checkbox,
             pn.pane.Markdown("---"), csv_button],
    main=[tabs], accent_base_color="#00f2ff", header_background="#1a1a1a")

dashboard.servable()
