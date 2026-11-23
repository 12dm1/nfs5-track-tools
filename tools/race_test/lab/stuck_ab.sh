#!/bin/bash
# stuck_ab.sh : reverse WITHOUT zones (where cars got stuck) on 4 tracks, stuck reset off vs on, side by side.
. "$(dirname "$0")/env.sh"
R=$WORK/reports/race_test/stuck_ab; mkdir -p $R/aside
mv GameData/Track/*_r.aispeed $R/aside/
COMMON="--track summit germany atlantic hills --car 993 --opponents 3 --runs 1 --parallel 4 --laps 1 --seconds 900 --time-scale 4 --seed 100 --load-timeout 300 --hang-seconds 60 --stagger 2 --keep-log --set reverse=1 --ini ai.stuck_player=1"
python -u $TOOLS/race_test/race_test.py $COMMON --ini ai.stuck_reset=0 --out $R/off > /dev/null 2>&1 &
sleep 4
python -u $TOOLS/race_test/race_test.py $COMMON --ini ai.stuck_reset=1 --out $R/on > /dev/null 2>&1 &
wait
mv $R/aside/*_r.aispeed GameData/Track/
echo "STUCK AB DONE" > $R/done.txt
