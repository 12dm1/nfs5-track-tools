#!/bin/bash
# full_speed_check.sh LINESET... : one timed lap at full speed (no .aispeed) per line set, one batch at a time.
. "$(dirname "$0")/env.sh"
R=$WORK/reports/race_test/full_check
LOG=$R/progress.txt; mkdir -p $R
TR="aquatica atlantic dolphin country empire france germany gt1 gt2 gt3 hills hometown lostcany park redrock rockypas snowy summit uk"
for L in "$@"; do
  cp $WORK/backups/lsp-$L/*.lsp GameData/Track/racer/work/
  python -u $TOOLS/race_test/race_test.py --track $TR --car 993 --opponents 3 --runs 1 --parallel 19 --laps 1 --seconds 600 --time-scale 4 --seed 100 --load-timeout 300 --hang-seconds 60 --stagger 1 --out $R/$L > /dev/null 2>&1
  python $TOOLS/race_test/lab/zone_stats_all.py $(ls -d $R/$L/*_batch | tail -1) "$L full speed" >> $LOG
done
echo "FULL CHECK DONE" >> $LOG
