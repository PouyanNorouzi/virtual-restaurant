#!/usr/bin/env bash
# (Re)generates backend/mosquitto/passwd using the eclipse-mosquitto docker
# image's mosquitto_passwd tool, so no local mosquitto install is required.
#
# Usage: ./generate_passwd.sh
#
# You will be prompted for a password for each of: backend, customer
# (see mosquitto/acl.conf for the matching ACL rules - "customer" is the
# single shared credential baked into every browser client's MQTT config;
# per-session isolation comes from %c-pattern ACLs, not from separate
# credentials per table/session). Passwords are stored bcrypt-hashed by
# mosquitto_passwd; the resulting passwd file is gitignored and must be
# distributed out-of-band (e.g. via your secrets manager) for any
# shared/deployed environment.

set -euo pipefail
cd "$(dirname "$0")"

USERS=(backend customer)
rm -f passwd
touch passwd

for user in "${USERS[@]}"; do
    echo "Setting password for '${user}':"
    docker run --rm -it \
        -v "$(pwd)/passwd:/mosquitto/config/passwd" \
        eclipse-mosquitto:2 \
        mosquitto_passwd /mosquitto/config/passwd "${user}"
done

echo "Wrote $(pwd)/passwd"
