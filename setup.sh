#!/usr/bin/env bash
# Wrapper for `python setup.py` (named setup.sh because ./setup is the package directory).
# Finds Python >= 3.9 and passes all arguments through.
set -eu
cd "$(dirname "$0")"

for cand in python3 python; do
  if command -v "$cand" >/dev/null 2>&1 && "$cand" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' 2>/dev/null; then
    exec "$cand" setup.py "$@"
  fi
done

echo "Python 3.9 or newer is required but was not found." >&2
echo "Options: install Python from https://www.python.org/downloads/, or skip local setup and use" >&2
echo "the GitHub Actions workflow or a GitHub Codespace (see the README)." >&2
exit 2
