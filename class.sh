#!/bin/bash
# Start or stop the class notes app plus its public HTTPS tunnel.
#   ./class.sh start   -> prints the URL to open on your phone/iPad
#   ./class.sh stop    -> takes it offline
cd "$(dirname "$0")"
LOG=/tmp/classnotes

case "$1" in
stop)
  pkill -f 'caffeinate -dis'
  sudo pmset -a disablesleep 0
  pkill -f 'cloudflared tunnel --url http://localhost:5005'
  lsof -ti tcp:5005 | xargs kill 2>/dev/null
  echo "Stopped. The tunnel URL is dead — nobody can reach your notes now."
  ;;
*)
  # Stay awake with the lid closed until './class.sh stop'. caffeinate alone
  # can't do it -- macOS clamshell-sleeps anyway -- so disable lid sleep too.
  echo "Keeping the Mac awake with the lid closed needs your password:"
  sudo pmset -a disablesleep 1 || exit 1
  pkill -f 'caffeinate -dis'
  caffeinate -dis &
  lsof -ti tcp:5005 >/dev/null || ./run.sh > $LOG.app.log 2>&1 &
  rm -f $LOG.tunnel.log
  cloudflared tunnel --url http://localhost:5005 > $LOG.tunnel.log 2>&1 &
  for _ in $(seq 30); do
    url=$(grep -o 'https://[a-z0-9-]*\.trycloudflare\.com' $LOG.tunnel.log | head -1)
    [ -n "$url" ] && break
    sleep 1
  done
  [ -z "$url" ] && { echo "Tunnel did not come up. See $LOG.tunnel.log"; exit 1; }
  echo
  echo "  On this Mac:        http://localhost:5005"
  echo "  On phone / iPad:    $url"
  echo
  echo "  Lid can stay closed — sleep is off until you run './class.sh stop'."
  echo "  Don't leave it in a bag like this; it will run hot and drain."
  echo "  Run './class.sh stop' when class ends."
  ;;
esac
