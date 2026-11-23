#!/bin/bash
# rev_finish.sh : reverse caps (2 batches), then checks: seed 200 (996) and 550 Spyder (seed 100).
. "$(dirname "$0")/env.sh"
bash $TOOLS/race_test/lab/rev_pipeline.sh caps 1
R=$WORK/reports/race_test/rev
TR="aquatica atlantic dolphin country empire france germany gt1 gt2 gt3 hills hometown lostcany park redrock rockypas snowy summit uk"
python -u $TOOLS/race_test/race_test.py --track $TR --car 993 --opponents 3 --runs 1 --parallel 19 --laps 1 --seconds 700 --time-scale 4 --seed 200 --load-timeout 300 --hang-seconds 60 --stagger 1 --set reverse=1 --out $R/check_s200 > /dev/null 2>&1
python $TOOLS/race_test/lab/zone_stats_all.py $(ls -d $R/check_s200/*_batch | tail -1) "rev check 996 s200" >> $R/progress.txt
python -u $TOOLS/race_test/race_test.py --track $TR --car 550 --spec 550Arace --aispec 550a --opponents 3 --runs 1 --parallel 19 --laps 1 --seconds 700 --time-scale 4 --seed 100 --load-timeout 300 --hang-seconds 60 --stagger 1 --set reverse=1 --out $R/check_550 > /dev/null 2>&1
python $TOOLS/race_test/lab/zone_stats_all.py $(ls -d $R/check_550/*_batch | tail -1) "rev check 550 s100" >> $R/progress.txt
echo "REV FINISH DONE" >> $R/progress.txt
