#!/bin/sh
# Runs the companion with the screen taken over, and ALWAYS gives it back to xochitl:
# on normal exit, crash, SIGTERM, or the SSH session dropping (SIGHUP).
# Pattern and the wakelock lesson from github.com/MaximeRivest/quill (scripts/takeover.sh, MIT).
DIR=$(cd "$(dirname "$0")" && pwd)
LOCK=/tmp/rmpp-companion.lock
LOG="$DIR/last.log"
# One session at a time. A second takeover while one runs is what made xochitl abort with
# "Failed to lock epframebuffer"; mkdir is atomic, so it doubles as the lock.
if ! mkdir "$LOCK" 2>/dev/null; then
    OLD=$(cat "$LOCK/pid" 2>/dev/null)
    if [ -n "$OLD" ] && kill -0 "$OLD" 2>/dev/null; then
        echo "companion: already running (pid $OLD), not starting another"
        exit 3
    fi
    rm -rf "$LOCK"; mkdir "$LOCK" || exit 3
fi
echo $$ > "$LOCK/pid"
# The PC may turn the front light to full (light-pad mode); this is what to put back.
FL=/sys/class/backlight/rm_frontlight
cat "$FL/brightness" > "$LOCK/light" 2>/dev/null
restore() {
    trap - EXIT INT TERM HUP
    # $APP is the tee at the end of the pipe, so stop the app itself by name too.
    [ -n "$APP" ] && { pkill -x rmpp-companion 2>/dev/null || killall rmpp-companion 2>/dev/null; wait "$APP" 2>/dev/null; }
    # The lock guarantees no other session owns the screen, so always leave xochitl running.
    if ! systemctl is-active -q xochitl; then
        # xochitl's unit has StartLimitBurst=4 per 10 min and OnFailure=emergency.target, so clear
        # the start counter before restarting it; repeated sessions must never trip that limit.
        systemctl reset-failed xochitl 2>/dev/null
        systemctl start xochitl || { sleep 2; systemctl reset-failed xochitl; systemctl start xochitl; }
    fi
    { echo rmpp-companion > /sys/power/wake_unlock; } 2>/dev/null
    [ -s "$LOCK/light" ] && cat "$LOCK/light" > "$FL/brightness" 2>/dev/null
    rm -f "$DIR"/trace.*
    rm -rf "$LOCK"
    echo "companion: screen returned to xochitl"
}
trap restore EXIT INT TERM HUP
# The app takes commands on stdin and quits on EOF. A background job in a non-interactive
# shell gets /dev/null as stdin, so keep the SSH stdin on fd 3 and hand it over explicitly.
exec 3<&0
# xochitl normally holds a wakelock; without one the tablet can autosleep mid-session and the
# resume path starts a second display engine on top of ours.
{ echo rmpp-companion > /sys/power/wake_lock; } 2>/dev/null
systemctl stop xochitl
rm -f /tmp/epframebuffer.lock
sleep 1
echo "companion: starting $(date)" > "$LOG"
QT_QUICK_BACKEND=epaper "$DIR/rmpp-companion" -platform "${COMPANION_PLATFORM:-epaper}" "$@" <&3 2>&1 | tee -a "$LOG" &
APP=$!
wait "$APP"
echo "companion: app exited" | tee -a "$LOG"
