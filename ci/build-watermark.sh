#!/bin/bash
# Builds the Guacamole watermark extension jar from charts/guacamole/files/watermark
# usage: ci/build-watermark.sh [output.jar], default charts/guacamole/files/watermark.jar
set -eu
cd "$(dirname "$0")/.."
out=${1:-charts/guacamole/files/watermark.jar}
# stored, sorted, fixed time and mode: the same sources give the same bytes
python3 - charts/guacamole/files/watermark "$out" <<'EOF'
import os, sys, zipfile
src, out = sys.argv[1], sys.argv[2]
with zipfile.ZipFile(out, "w", zipfile.ZIP_STORED) as z:
    for name in sorted(os.listdir(src)):
        info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
        info.create_system = 3
        info.external_attr = 0o100644 << 16
        with open(os.path.join(src, name), "rb") as f:
            z.writestr(info, f.read())
EOF
echo "$out"
