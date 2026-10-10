#!/usr/bin/env sh
set -eu
cd "$(dirname "$0")"

# Git Bash uses the Windows launcher and its Scripts-based environment.
case "$(uname -s)" in
  MINGW*|MSYS*|CYGWIN*) MSYS_NO_PATHCONV=1 exec cmd.exe /c run_spritesheet_assistant.bat ;;
esac

if ! .venv/bin/python -c 'import sys; assert sys.version_info.major == 3' >/dev/null 2>&1; then
  if [ -e .venv ]; then
    backup="$(mktemp -d .venv-backup-XXXXXXXX)"
    mv .venv "$backup/environment"
    echo "Preserved the unusable environment in $backup/environment."
  fi
  echo "First-run setup: creating the private Python environment..."
  if python3 -c 'import sys; assert sys.version_info.major == 3' >/dev/null 2>&1; then
    python3 -m venv .venv
  elif python -c 'import sys; assert sys.version_info.major == 3' >/dev/null 2>&1; then
    python -m venv .venv
  else
    echo "Python 3 was not found. Install Python 3 and run this file again." >&2
    exit 1
  fi
fi

if ! .venv/bin/python -c 'import PIL, numpy' >/dev/null 2>&1; then
  .venv/bin/python -m pip install --disable-pip-version-check -r requirements.txt
fi

exec .venv/bin/python app.py
