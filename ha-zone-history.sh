#!/bin/zsh
set -eu

HA_URL="${HA_URL:-http://homeassistant.localdomain:8123}"
trap 'unset HA_TOKEN' EXIT

if [[ -z "${HA_TOKEN:-}" ]]; then
    read -r -s 'HA_TOKEN?Home Assistant token: ' </dev/tty >&2
    printf '\n' >&2
fi

if [[ -z "$HA_TOKEN" ]]; then
    printf 'Token cannot be empty.\n' >&2
    exit 1
fi

START=$(date -u -v-7d '+%Y-%m-%dT%H:%M:%SZ')
END=$(date -u '+%Y-%m-%dT%H:%M:%SZ')

printf 'Authorization: Bearer %s\n' "$HA_TOKEN" |
    curl --fail-with-body --silent --show-error --get \
        "${HA_URL%/}/api/history/period/$START" \
        --data-urlencode 'filter_entity_id=device_tracker.erlings_iphone_3' \
        --data-urlencode "end_time=$END" \
        --data-urlencode 'significant_changes_only=0' \
        --data-urlencode 'no_attributes=1' \
        --header @-
