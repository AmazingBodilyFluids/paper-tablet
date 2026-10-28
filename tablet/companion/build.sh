#!/bin/sh
# Cross-build with reMarkable's official Paper Pro (ferrari) SDK. Usage: SDK=/path/to/sdk ./build.sh
set -e
cd "$(dirname "$0")"
unset LD_LIBRARY_PATH
. "$SDK"/environment-setup-cortexa53-crypto-remarkable-linux
$CXX -O2 -std=c++17 -fPIC -Wall main.cpp companion_qml.cpp -o rmpp-companion \
    $(pkg-config --cflags --libs Qt6Quick Qt6Qml Qt6Gui Qt6Core)
$STRIP rmpp-companion 2>/dev/null || true
ls -l rmpp-companion
