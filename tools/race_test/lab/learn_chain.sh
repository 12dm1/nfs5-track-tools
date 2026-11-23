#!/bin/bash
# learn_chain.sh BASE N [learn args...] : N learning iterations on top of variant BASE
BASE=$1; N=$2; shift 2
SP="$(cd "$(dirname "$0")" && pwd)"
R=$WORK/reports/race_test
. "$(dirname "$0")/env.sh"
cp $WORK/backups/lsp-$BASE/*.lsp GameData/Track/racer/work/
PREV=$BASE
for i in $(seq 1 $N); do
  NAME=${BASE}L$i
  B1=$(ls -d $R/line_$PREV/s100/*_batch | tail -1); B2=$(ls -d $R/line_$PREV/s200/*_batch | tail -1)
  (cd $TOOLS/track && PYTHONIOENCODING=utf-8 python learn_line.py --game "$GAME" "$@" $B1 $B2) > $R/line_$NAME.learn.txt 2>&1
  mkdir -p $WORK/backups/lsp-$NAME && cp GameData/Track/racer/work/{aquatica,atlantic,dolphin,country,empire,france,germany,gt1,gt2,gt3,hills,hometown,lostcany,park,redrock,rockypas,snowy,summit,uk}[01].lsp $WORK/backups/lsp-$NAME/
  bash "$SP/variant.sh" $NAME
  PREV=$NAME
done
echo "LEARN CHAIN $BASE DONE"
