#!/bin/bash
# rev_pipeline.sh PHASE [N] : AI tuning for REVERSE races (REVERSE=1) on the 19 converted tracks.
# One batch (19 games, seed 100, one timed lap) per step. Touches only <track>1.lsp and
# <track>_r.aispeed; the forward line/zones stay.
#   base        baseline: reverse lines as installed, no reverse zones
#   line N      slow mode (every _r.aispeed "0 99999 2.0"), N x learn_line --reverse; backups lsp-RSLOW<i>
#   zones N     full speed, N x learn_zones --reverse (x1.25 + ramps); backups aispeed-RZ<i>
#   caps N      N x learn_caps --reverse; backups aispeed-RC<i>
#   check NAME  one batch with what is installed
PHASE=$1; N=${2:-3}
. "$(dirname "$0")/env.sh"
R=$WORK/reports/race_test/rev
LOG=$R/progress.txt; mkdir -p $R
TR="aquatica atlantic dolphin country empire france germany gt1 gt2 gt3 hills hometown lostcany park redrock rockypas snowy summit uk"
race() {   # race OUT LABEL
  python -u $TOOLS/race_test/race_test.py --track $TR --car 993 --opponents 3 --runs 1 --parallel 19 --laps 1 --seconds 700 --time-scale 4 --seed 100 --load-timeout 300 --hang-seconds 60 --stagger 1 --set reverse=1 --out $1 > /dev/null 2>&1
  B=$(ls -d $1/*_batch | tail -1)
  python $TOOLS/race_test/lab/zone_stats_all.py $B "$2" >> $LOG
}
case $PHASE in
  base)
    race $R/base "rev base" ;;
  line)
    for t in $TR; do printf '# AI4b temporary slow mode\n0 99999 2.0\n' > GameData/Track/${t}_r.aispeed; done
    for i in $(seq 0 $N); do
      race $R/line$i "rev slow line it$i"
      if [ $i -lt $N ]; then
        (cd $TOOLS/track && PYTHONIOENCODING=utf-8 python learn_line.py --game "$GAME" --reverse --kinds wall,hit3 --gain 0.5 --max-step 3.0 $B) > $R/line$i/learn.txt 2>&1
        mkdir -p $WORK/backups/lsp-RSLOW$((i+1)) && cp GameData/Track/racer/work/*1.lsp $WORK/backups/lsp-RSLOW$((i+1))/
      fi
    done
    for t in $TR; do rm -f GameData/Track/${t}_r.aispeed; done ;;
  zones)
    for i in $(seq 0 $N); do
      race $R/zones$i "rev zones it$i"
      if [ $i -lt $N ]; then
        for t in $TR; do python $TOOLS/race_test/learn_zones.py --game "$GAME" --track $t --reverse --min-events 1 --step 0.25 $B; done > $R/zones$i/learn.txt 2>&1
        mkdir -p $WORK/backups/aispeed-RZ$((i+1)) && cp GameData/Track/*_r.aispeed $WORK/backups/aispeed-RZ$((i+1))/
      fi
    done ;;
  caps)
    for i in $(seq 0 $N); do
      race $R/caps$i "rev caps it$i"
      if [ $i -lt $N ]; then
        for t in $TR; do python $TOOLS/race_test/learn_caps.py --game "$GAME" --track $t --reverse $B; done > $R/caps$i/learn.txt 2>&1
        mkdir -p $WORK/backups/aispeed-RC$((i+1)) && cp GameData/Track/*_r.aispeed $WORK/backups/aispeed-RC$((i+1))/
      fi
    done ;;
  check)
    race $R/check_$N "rev check $N" ;;
esac
echo "REV $PHASE DONE" >> $LOG
