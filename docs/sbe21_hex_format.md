# SBE 21 raw `.hex` scan format (historical notes)

The flow-through pipeline originally read the raw Seasave `.hex` files from the SBE 21 (S/N 3507) and
now reads only the `.cnv` files written by SBE Data Processing. The `.hex` reader and the `.xmlcon`
calibration code were removed from `sbe21_ingestion.py` (they are in the git history before the cleanup commit).
These notes record what was learned about the raw format in case it is ever needed again.

A `.hex` scan, as decoded from the 2025 Baja files, held:

* raw channel counts (no calibration applied)
* NMEA latitude and longitude appended to every scan by Seasave
* NMEA depth (only present in the longer row format)
* a UTC timestamp for every scan (built from the header start time and the scan rate)

There is no pressure sensor, so the profiling-CTD steps (soak, loop edit, depth binning) never applied.

## Row formats (baja2025)

All hex, one scan per line, after the `*END*` line:

| Characters | Meaning |
|---|---|
| `[ 0: 4]` | ch0 count |
| `[ 4: 8]` | ch1 count |
| `[ 8:12]` | ch2 count |
| `[12:16]` | ch3 raw word |
| `[16:20]` | ch4 raw word |
| `[20:24]` | ch5 raw word (always 0) |
| `[24:26]` | aux byte |
| `[26:32]` | latitude = int / 50000 (degrees, sign from the header) |
| `[32:38]` | longitude = int / 50000 (degrees, sign from the header) |
| `[38:40]` | flag byte (0x40 / 0x41) |
| `[40:46]` | NMEA depth, decimetres (0 = none). Only in the 46-character format |

Rows are 40 characters (no depth) or 46 characters (with NMEA depth).

## Status of the field decoding

* **Verified:** latitude and longitude (they match the header NMEA position).
* **Probable:** depth (plausible metres, missing while the EK80 is running).
* **Unknown:** ch0 to ch5 and the aux byte. ch0 and ch1 behave like temperature and conductivity counts and ch2 tracks ch0, but this was not confirmed. Converting to engineering units needs the SBE 21 calibration for S/N 3507 (`.xmlcon` or `.cal`), and no coefficients were guessed.

## Why `.cnv` replaced it

SBE Data Processing writes temperature, conductivity, salinity, position and time already converted, with the sensor
configuration embedded in the header. Using those values exactly as written avoids reimplementing the conversion,
and the `.hex` and `.xmlcon` files are needed only upstream, to create the `.cnv` files.
