#!/bin/bash
# zone_tune_loop.sh N [DECAY] : keep tuning the installed zones, one batch (19 games, 1 lap) per
# iteration, seeds alternating 100/200. Spots with trouble are raised (x1.15 + ramp); zones clean
# in this and the previous batch give back (1 - DECAY) of their slowdown, to win the lap time back.
N=$1; DECAY=${2:-0.9}
. "$(dirname "$0")/env.sh"
R=$WORK/reports/race_test/zones_tune
LOG=$R/progress.txt; mkdir -p $R
TR="aquatica atlantic dolphin country empire france germany gt1 gt2 gt3 hills hometown lostcany park redrock rockypas snowy summit uk"
PREV="${INIT_PREV:-}"
for i in $(seq 0 $N); do
  SEED=$(( i % 2 == 0 ? ${FIRST_SEED:-100} : 300 - ${FIRST_SEED:-100} ))
  mkdir -p $R/it$i/zones; cp GameData/Track/*.aispeed $R/it$i/zones/
  python -u $TOOLS/race_test/race_test.py --track $TR --car 993 --opponents 3 --runs 1 --parallel 19 --laps 1 --seconds 600 --time-scale 4 --seed $SEED --load-timeout 300 --hang-seconds 60 --stagger 1 --out $R/it$i > /dev/null 2>&1
  B=$(ls -d $R/it$i/*_batch | tail -1)
  python $TOOLS/race_test/lab/zone_stats_all.py $B "it$i s$SEED" >> $LOG
  if [ $i -lt $N ]; then
    for t in $TR; do python $TOOLS/race_test/learn_zones.py --game "$GAME" --track $t --min-events 1 --decay $DECAY ${PREV:+--history $PREV} -- $B; done > $R/it$i/learn.txt 2>&1
  fi
  PREV=$B
done
echo "ZONE TUNE LOOP DONE" >> $LOG
