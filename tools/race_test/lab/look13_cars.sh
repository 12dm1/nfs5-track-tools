#!/bin/bash
# look13_cars.sh : [ai] lookahead_scale=1.3 on all 19 tracks for 996 (default), 911 GT1, 550 Spyder.
. "$(dirname "$0")/env.sh"
R=$WORK/reports/race_test/look13
LOG=$R/progress.txt; mkdir -p $R
TR="aquatica atlantic dolphin country empire france germany gt1 gt2 gt3 hills hometown lostcany park redrock rockypas snowy summit uk"
for C in 993:: gt1:GT1race:Gt1 550:550Arace:550a; do
  IFS=: read CAR SPEC AISPEC <<< "$C"
  python -u $TOOLS/race_test/race_test.py --track $TR --car $CAR ${SPEC:+--spec $SPEC} ${AISPEC:+--aispec $AISPEC} --opponents 3 --runs 1 --parallel 19 --laps 1 --seconds 700 --time-scale 4 --seed 100 --load-timeout 300 --hang-seconds 60 --stagger 1 --ini ai.lookahead_scale=1.3 --out $R/${SPEC:-default} > /dev/null 2>&1
  python $TOOLS/race_test/lab/zone_stats_all.py $(ls -d $R/${SPEC:-default}/*_batch | tail -1) "look13 ${SPEC:-default}" >> $LOG
done
echo "LOOK13 DONE" >> $LOG
