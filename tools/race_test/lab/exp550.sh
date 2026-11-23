#!/bin/bash
# exp550.sh : three 550 Spyder variants side by side on its 6 worst tracks (3 x 6 = 18 games).
. "$(dirname "$0")/env.sh"
R=$WORK/reports/race_test/exp550
LOG=$R/progress.txt; mkdir -p $R
TR="aquatica snowy gt3 summit park germany"
COMMON="--track $TR --car 550 --spec 550Arace --aispec 550a --opponents 3 --runs 1 --parallel 6 --laps 1 --seconds 700 --time-scale 4 --seed 100 --load-timeout 300 --hang-seconds 60 --stagger 2"
python -u $TOOLS/race_test/race_test.py $COMMON --ini ai.lookahead_scale=1.3 --out $R/look13 > /dev/null 2>&1 &
sleep 3
python -u $TOOLS/race_test/race_test.py $COMMON --set car[0].traction=1 --set car[1].traction=1 --set car[2].traction=1 --set car[3].traction=1 --out $R/traction > /dev/null 2>&1 &
sleep 3
python -u $TOOLS/race_test/race_test.py $COMMON --ini ai.target_speed_scale=0.9 --out $R/speed90 > /dev/null 2>&1 &
wait
for v in look13 traction speed90; do python $TOOLS/race_test/lab/zone_stats_all.py $(ls -d $R/$v/*_batch | tail -1) "$v" >> $LOG; done
echo "EXP550 DONE" >> $LOG
