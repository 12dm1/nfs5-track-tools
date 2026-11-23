#!/bin/bash
# zone_all_loop.sh N : AI4 speed zones for all 19 converted tracks at once.
# Each iteration: one batch (19 games, seed 100, one timed lap - the races are
# deterministic, one run shows every repeatable contact), then learn_zones.py raises
# the zone factor before every spot with a wall/object contact, stall or spin.
N=$1
. "$(dirname "$0")/env.sh"
R=$WORK/reports/race_test/zones_all
LOG=$R/progress.txt; mkdir -p $R
TR="aquatica atlantic dolphin country empire france germany gt1 gt2 gt3 hills hometown lostcany park redrock rockypas snowy summit uk"
for i in $(seq 0 $N); do
  python -u $TOOLS/race_test/race_test.py --track $TR --car 993 --opponents 3 --runs 1 --parallel 19 --laps 1 --seconds 600 --time-scale 4 --seed 100 --load-timeout 300 --hang-seconds 60 --stagger 1 --out $R/it$i > /dev/null 2>&1
  B=$(ls -d $R/it$i/*_batch | tail -1)
  mkdir -p $R/it$i/zones; cp GameData/Track/*.aispeed $R/it$i/zones/ 2>/dev/null
  python $TOOLS/race_test/lab/zone_stats_all.py $B "it$i" >> $LOG
  if [ $i -lt $N ]; then
    for t in $TR; do python $TOOLS/race_test/learn_zones.py --game "$GAME" --track $t --min-events 1 $B; done > $R/it$i/learn.txt 2>&1
  fi
done
echo "ZONE ALL LOOP DONE" >> $LOG
