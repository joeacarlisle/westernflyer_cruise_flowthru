#!/bin/bash
#
# Build every cruise under cruises/ into processed/wf_flowthru.duckdb, one after another.
#
# Usage:   ./flowthru_build_all.sh [extra args forwarded to main.py, e.g. --verbose]
# Each cruise is a folder under cruises/ whose name is the cruise ID.
# Add a new cruise by creating its folder; no edit to this script is needed.
# Each cruise is built from its .cnv files only; a cruise with no .cnv files fails (see the log).
#
# Make executable once with:  chmod +x scripts/linux_mac/*.sh

cd "$(dirname "$0")/../.."

if [ -f ".venv/bin/activate" ]; then
    source .venv/bin/activate
else
    echo "[WARNING] Virtual environment not found at .venv/bin/activate"
fi

failed=()
for dir in cruises/*/; do
    cruise="$(basename "$dir")"
    echo
    echo "===== Building ${cruise} ====="
    python main.py "${cruise}" "$@" || failed+=("${cruise}")
done

echo
if [ "${#failed[@]}" -gt 0 ]; then
    echo "Build finished with failures: ${failed[*]}"
    echo "Check the newest files in logs/."
    read -p "Press any key to continue..."
    exit 1
fi
echo "All cruises built successfully."
read -p "Press any key to continue..."
