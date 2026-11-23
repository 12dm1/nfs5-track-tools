# Sourced by the lab scripts: GAME, WORK, TOOLS, ... from nfs5tools.ini / the environment
# (tools/track/nfs5cfg.py), then the working directory is the game folder.
LAB_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
eval "$(python "$LAB_DIR/../../track/nfs5cfg.py" --sh)"
[ -d "$GAME/GameData" ] || { echo "set [paths] game in nfs5tools.ini or NFS5_GAME" >&2; exit 1; }
cd "$GAME" || exit 1
