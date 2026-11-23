#!/bin/bash
# fast_line_loop.sh N : one seed, one timed lap per iteration, 19 tracks at once;
# move the forward lines away from every wall/object contact (gain 0.5, <= 3 m), repeat.
N=$1
. "$(dirname "$0")/env.sh"
R=$WORK/reports/race_test/fast_line
LOG=$R/progress.txt
mkdir -p $R
TR="aquatica atlantic dolphin country empire france germany gt1 gt2 gt3 hills hometown lostcany park redrock rockypas snowy summit uk"
for i in $(seq 0 $N); do
  python -u $TOOLS/race_test/race_test.py --track $TR --car 993 --opponents 3 --runs 1 --parallel 19 --laps 1 --seconds 600 --time-scale 4 --seed 100 --load-timeout 300 --hang-seconds 60 --stagger 1 --out $R/it$i > /dev/null 2>&1
  B=$(ls -d $R/it$i/*_batch | tail -1)
  python $TOOLS/race_test/lab/zone_stats_all.py $B "it$i" >> $LOG
  if [ $i -lt $N ]; then
    (cd $TOOLS/track && PYTHONIOENCODING=utf-8 python learn_line.py --game "$GAME" --kinds wall,hit3 --gain 0.5 --max-step 3.0 $B) > $R/it$i/learn.txt 2>&1
    mkdir -p $WORK/backups/lsp-FAST$((i+1)) && cp GameData/Track/racer/work/*[01].lsp $WORK/backups/lsp-FAST$((i+1))/ 2>/dev/null
  fi
done
echo "FAST LINE LOOP DONE" >> $LOG
