#!/bin/bash
# check_cars.sh NAME SEED MODEL:SPEC:AISPEC... : the installed zones with other cars (player +
# 3 opponents, same model/physics/AI tuning), one lap on all 19 tracks, one batch at a time.
NAME=$1; SEED=$2; shift 2
. "$(dirname "$0")/env.sh"
R=$WORK/reports/race_test/cars_$NAME
LOG=$R/progress.txt; mkdir -p $R
TR="${TRACKS:-aquatica atlantic dolphin country empire france germany gt1 gt2 gt3 hills hometown lostcany park redrock rockypas snowy summit uk}"
for C in "$@"; do
  IFS=: read CAR SPEC AISPEC <<< "$C"
  python -u $TOOLS/race_test/race_test.py --track $TR --car $CAR ${SPEC:+--spec $SPEC} ${AISPEC:+--aispec $AISPEC} --opponents 3 --runs 1 --parallel 19 --laps 1 --seconds 700 --time-scale 4 --seed $SEED --load-timeout 300 --hang-seconds 60 --stagger 1 ${EXTRA} --out $R/$CAR > /dev/null 2>&1
  python $TOOLS/race_test/lab/zone_stats_all.py $(ls -d $R/$CAR/*_batch | tail -1) "$NAME $CAR s$SEED" >> $LOG
done
echo "CARS $NAME DONE" >> $LOG
