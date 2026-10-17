#!/bin/sh
# Xvnc on port 5900 with the password from VNC_PASSWORD, and Firefox in kiosk
# mode on it, started again whenever it exits. No clipboard crosses the VNC
# connection in either direction. Runs as any user id
set -eu
export HOME=/tmp DISPLAY=:1
GEOMETRY=${GEOMETRY:-1600x900}
HOMEPAGE=${HOMEPAGE:-about:blank}

mkdir -p /tmp/.vnc /tmp/.X11-unix
chmod 1777 /tmp/.X11-unix 2>/dev/null || true
umask 077
printf '%s\n' "$VNC_PASSWORD" | vncpasswd -f > /tmp/.vnc/passwd
umask 022
# Xvnc and Firefox start without the password in their environment
unset VNC_PASSWORD

Xvnc :1 -geometry "$GEOMETRY" -depth 24 -rfbport 5900 -SecurityTypes VncAuth \
  -PasswordFile /tmp/.vnc/passwd -AlwaysShared -AcceptSetDesktopSize=0 \
  -SendCutText=0 -AcceptCutText=0 -SendPrimary=0 -SetPrimary=0 \
  -nolisten tcp &
xvnc=$!
# read by the exec probes, which open no VNC connection
echo $xvnc > /tmp/xvnc.pid
ff=""
trap 'kill $ff $xvnc 2>/dev/null; exit 0' TERM INT

until [ -e /tmp/.X11-unix/X1 ]; do
  kill -0 $xvnc 2>/dev/null || { echo "Xvnc exited"; exit 1; }
  sleep 1
done

while kill -0 $xvnc 2>/dev/null; do
  # a lock left by the previous pod would make Firefox refuse the profile
  rm -f /profile/lock /profile/.parentlock
  # typed form values and the tabs of the previous run; saved logins stay
  rm -rf /profile/formhistory.sqlite* /profile/sessionstore.jsonlz4 /profile/sessionstore-backups
  # no window manager applies --kiosk full screen: size the window to the desktop
  firefox --kiosk --no-remote --profile /profile \
    --width "${GEOMETRY%x*}" --height "${GEOMETRY#*x}" "$HOMEPAGE" &
  ff=$!
  echo $ff > /tmp/firefox.pid
  wait $ff || true
  echo "firefox exited, starting it again"
  sleep 2
done
echo "Xvnc exited"
exit 1
