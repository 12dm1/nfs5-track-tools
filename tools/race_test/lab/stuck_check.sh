#!/bin/bash
# stuck_check.sh : AI5 stuck reset, forward and reverse, 996 default, seed 100, one lap, 19 tracks.
. "$(dirname "$0")/env.sh"
R=$WORK/reports/race_test/stuck
LOG=$R/progress.txt; mkdir -p $R
TR="aquatica atlantic dolphin country empire france germany gt1 gt2 gt3 hills hometown lostcany park redrock rockypas snowy summit uk"
COMMON="--track $TR --car 993 --opponents 3 --runs 1 --parallel 19 --laps 1 --seconds 700 --time-scale 4 --seed 100 --load-timeout 300 --hang-seconds 60 --stagger 1 --keep-log --ini ai.stuck_reset=1 --ini ai.stuck_player=1"
python -u $TOOLS/race_test/race_test.py $COMMON --out $R/fwd > /dev/null 2>&1
python $TOOLS/race_test/lab/zone_stats_all.py $(ls -d $R/fwd/*_batch | tail -1) "stuck fwd" >> $LOG
python -u $TOOLS/race_test/race_test.py $COMMON --set reverse=1 --out $R/rev > /dev/null 2>&1
python $TOOLS/race_test/lab/zone_stats_all.py $(ls -d $R/rev/*_batch | tail -1) "stuck rev" >> $LOG
echo "STUCK DONE" >> $LOG
