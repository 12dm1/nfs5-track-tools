#!/usr/bin/env bash
# Track editor (Linux/macOS/Git Bash): ./run_track_editor.sh <track> [--ai] [--atmosphere file.ini]
set -e
cd "$(dirname "$0")"
PY=${PYTHON:-$(command -v python3 || command -v python)}
eval "$("$PY" tools/track/nfs5cfg.py --sh)"
if [ ! -d "$GAME/GameData/Track" ]; then
    echo "Porsche Unleashed folder not found: '$GAME'. Set [paths] game in nfs5tools.ini or NFS5_GAME." >&2
    exit 1
fi
if [ -z "$1" ]; then
    echo "usage: $0 <track> [--ai] [--atmosphere file.ini]"
    echo "tracks:"; ls "$GAME/GameData/Track" | sed -n 's/^\([^.]*\)\.crp$/  \1/p'
    exit 1
fi
export PYTHONIOENCODING=utf-8
exec "$PY" tools/track_editor "$GAME/GameData/Track" "$@"
