#!/bin/sh
# Cross-build with reMarkable's official SDK for one model. Usage:
#   SDK=/path/to/ferrari-sdk ./build.sh            -> rmpp-companion       (Paper Pro)
#   SDK=/path/to/rm2-sdk MODEL=rm2 ./build.sh      -> rmpp-companion-rm2   (reMarkable 2)
#   SDK=/path/to/rm1-sdk MODEL=rm1 ./build.sh      -> rmpp-companion-rm1   (reMarkable 1)
set -e
cd "$(dirname "$0")"
unset LD_LIBRARY_PATH
. "$(ls "$SDK"/environment-setup-* | head -1)"
OUT=rmpp-companion${MODEL:+-$MODEL}
$CXX -O2 -std=c++17 -fPIC -Wall main.cpp companion_qml.cpp -o "$OUT" \
    $(pkg-config --cflags --libs Qt6Quick Qt6Qml Qt6Gui Qt6Core)
$STRIP "$OUT" 2>/dev/null || true
ls -l "$OUT"
