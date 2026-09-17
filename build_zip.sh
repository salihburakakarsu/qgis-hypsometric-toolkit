#!/usr/bin/env bash
# Package the plugin as hypsometric_toolkit.zip (for QGIS "Install from ZIP")
# Usage: ./build_zip.sh
set -e
cd "$(dirname "$0")"
rm -f hypsometric_toolkit.zip
zip -r hypsometric_toolkit.zip hypsometric_toolkit \
    -x "*/__pycache__/*" -x "*.pyc" -x "*.DS_Store"
echo "Built: $(pwd)/hypsometric_toolkit.zip"
