#!/usr/bin/env bash
# Run the CLI with the project's virtualenv: ./phones.sh best "oppo find n6"
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "$DIR/.venv/bin/python" -m phones "$@"
