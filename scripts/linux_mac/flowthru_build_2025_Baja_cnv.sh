#!/usr/bin/env bash
# Build 2025_Baja, adding temperature / conductivity / salinity from the .cnv files in
# cruises/2025_Baja/cnv.  Files without a .cnv are loaded as raw counts.
# Add --require-cnv to stop if any .hex file has no .cnv.
exec "$(dirname "$0")/flowthru_build.sh" 2025_Baja "$@"
