#!/bin/sh
# Build-time helper for restricted networks. `begin` trusts any *.crt mounted at /extra-ca and, when asked,
# switches apt to https; `end` removes them again within the same RUN so nothing is retained in a layer.
set -eu
case "${1:-}" in
  begin)
    if ls /extra-ca/*.crt >/dev/null 2>&1; then
      cp /extra-ca/*.crt /usr/local/share/ca-certificates/ && update-ca-certificates >/dev/null
    fi
    if [ -f /etc/apt/sources.list.d/debian.sources ]; then
      sed -i "s#http://deb.debian.org#${APT_SCHEME:-http}://deb.debian.org#g" /etc/apt/sources.list.d/debian.sources
    fi ;;
  end)
    rm -f /usr/local/share/ca-certificates/*.crt
    update-ca-certificates --fresh >/dev/null 2>&1 || true
    rm -rf /var/lib/apt/lists/* /root/.cache ;;
  *) echo "usage: kleos-net begin|end" >&2; exit 2 ;;
esac
