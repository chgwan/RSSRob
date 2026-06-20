#!/usr/bin/env bash
# Renew an mkcert-issued TLS certificate in place when it nears expiry.
#
# Usage:
#   renew-cert.sh <cert.pem> <key.pem> <mkcert> <service|-> <san>...
#     cert.pem   cert to renew (rewritten in place)
#     key.pem    matching private key (rewritten in place)
#     mkcert     path to the mkcert binary
#     service    systemd --user unit to restart after a real renewal, or "-"
#                to skip the restart
#     san...     certificate SANs forwarded verbatim to mkcert
#
# Renews only when the cert has <= RENEW_DAYS (default 30) days of validity
# left; otherwise prints a note and exits 0 without touching anything, so it
# is safe to run frequently from a systemd timer. Designed to back the personal
# rssrob-renew-cert.{service,timer} units (~/.config/systemd/user/), which point
# this at var/dev.{crt,key} for the rssrob-web service.
set -euo pipefail

cert="${1:?usage: renew-cert.sh <cert> <key> <mkcert> <service|-> <san>...}"
key="${2:?key path required}"
mkcert_bin="${3:?mkcert path required}"
service="${4:?systemd unit or '-' required}"
shift 4
sans=("$@")

renew_days="${RENEW_DAYS:-30}"

# Days until the cert's notAfter date (GNU date parses the openssl timestamp).
enddate="$(openssl x509 -in "$cert" -noout -enddate | cut -d= -f2)"
end_epoch="$(date -d "$enddate" +%s)"
days_left=$(( (end_epoch - $(date +%s)) / 86400 ))

if [ "$days_left" -gt "$renew_days" ]; then
  echo "renew-cert: $cert has ${days_left}d left (threshold ${renew_days}d); skipping"
  exit 0
fi

echo "renew-cert: $cert has ${days_left}d left; renewing (SANs: ${sans[*]})"
"$mkcert_bin" -cert-file "$cert" -key-file "$key" "${sans[@]}"
if [ "$service" != "-" ]; then
  systemctl --user restart "$service"
  echo "renew-cert: restarted $service"
fi
echo "renew-cert: done"
