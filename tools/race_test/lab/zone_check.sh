#!/bin/bash
# zone_check.sh SEED : one lap on all 19 tracks with the installed zones, then without (sequential).
SEED=$1
. "$(dirname "$0")/env.sh"
R=$WORK/reports/race_test/zone_check
LOG=$R/progress.txt; mkdir -p $R
TR="aquatica atlantic dolphin country empire france germany gt1 gt2 gt3 hills hometown lostcany park redrock rockypas snowy summit uk"
run() {
  python -u $TOOLS/race_test/race_test.py --track $TR --car 993 --opponents 3 --runs 1 --parallel 19 --laps 1 --seconds 600 --time-scale 4 --seed $SEED --load-timeout 300 --hang-seconds 60 --stagger 1 --out $R/$1_s$SEED > /dev/null 2>&1
  python $TOOLS/race_test/lab/zone_stats_all.py $(ls -d $R/$1_s$SEED/*_batch | tail -1) "$1 seed $SEED" >> $LOG
}
run zones
mkdir -p $R/aside; mv GameData/Track/*.aispeed $R/aside/
run nozones
mv $R/aside/*.aispeed GameData/Track/
echo "ZONE CHECK $SEED DONE" >> $LOG
