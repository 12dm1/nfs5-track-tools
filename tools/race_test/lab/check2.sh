#!/bin/bash
# check2.sh NAME : the installed zones on seeds 100 and 200, one lap, 19 tracks, one batch at a time.
. "$(dirname "$0")/env.sh"
R=$WORK/reports/race_test/check_$1
LOG=$R/progress.txt; mkdir -p $R
TR="aquatica atlantic dolphin country empire france germany gt1 gt2 gt3 hills hometown lostcany park redrock rockypas snowy summit uk"
for SEED in 100 200; do
  python -u $TOOLS/race_test/race_test.py --track $TR --car 993 --opponents 3 --runs 1 --parallel 19 --laps 1 --seconds 600 --time-scale 4 --seed $SEED --load-timeout 300 --hang-seconds 60 --stagger 1 --out $R/s$SEED > /dev/null 2>&1
  python $TOOLS/race_test/lab/zone_stats_all.py $(ls -d $R/s$SEED/*_batch | tail -1) "$1 s$SEED" >> $LOG
done
echo "CHECK $1 DONE" >> $LOG
