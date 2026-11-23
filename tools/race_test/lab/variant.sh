#!/bin/bash
# variant.sh NAME [apply_lsp args...]   (no args = keep the installed lines)
# Runs the 19 HS tracks, seeds 100 and 200, one batch of 19 games at a time.
NAME=$1; shift
. "$(dirname "$0")/env.sh"
if [ $# -gt 0 ]; then
  (cd $TOOLS/track && PYTHONIOENCODING=utf-8 python apply_lsp.py --game "$GAME" "$@" Aquatica Atlantic Coastal Country Empire France Germany Gt1 Gt2 Gt3 Hills Hometown Lostcany Park Redrock Rockypas Snowy Summit Uk) > $WORK/reports/race_test/line_$NAME.apply.txt 2>&1
  mkdir -p $WORK/backups/lsp-$NAME && cp GameData/Track/racer/work/{aquatica,atlantic,dolphin,country,empire,france,germany,gt1,gt2,gt3,hills,hometown,lostcany,park,redrock,rockypas,snowy,summit,uk}[01].lsp $WORK/backups/lsp-$NAME/
fi
T="aquatica atlantic dolphin country empire france germany gt1 gt2 gt3 hills hometown lostcany park redrock rockypas snowy summit uk"
for SEED in ${SEEDS:-100 200}; do
  python -u $TOOLS/race_test/race_test.py --track $T --car 993 --opponents 3 --runs 1 --parallel 19 --laps 2 --seconds 700 --time-scale 4 --seed $SEED --load-timeout 300 --hang-seconds 60 --stagger 2 --out $WORK/reports/race_test/line_$NAME/s$SEED > /dev/null 2>&1
done
echo "VARIANT $NAME DONE"
