#!/bin/bash
# cap_loop.sh N [FIRST_SEED] : AI4c speed caps. One batch per iteration (19 tracks, 1 lap,
# seeds alternating), then learn_caps.py lowers the caps where cars still crash or spin.
N=$1; S0=${2:-200}
. "$(dirname "$0")/env.sh"
R=$WORK/reports/race_test/caps
LOG=$R/progress.txt; mkdir -p $R
TR="aquatica atlantic dolphin country empire france germany gt1 gt2 gt3 hills hometown lostcany park redrock rockypas snowy summit uk"
for i in $(seq 0 $N); do
  SEED=$(( i % 2 == 0 ? S0 : 300 - S0 ))
  mkdir -p $R/it$i/zones; cp GameData/Track/*.aispeed $R/it$i/zones/
  python -u $TOOLS/race_test/race_test.py --track $TR --car 993 --opponents 3 --runs 1 --parallel 19 --laps 1 --seconds 600 --time-scale 4 --seed $SEED --load-timeout 300 --hang-seconds 60 --stagger 1 --out $R/it$i > /dev/null 2>&1
  B=$(ls -d $R/it$i/*_batch | tail -1)
  python $TOOLS/race_test/lab/zone_stats_all.py $B "it$i s$SEED" >> $LOG
  if [ $i -lt $N ]; then
    for t in $TR; do python $TOOLS/race_test/learn_caps.py --game "$GAME" --track $t $B; done > $R/it$i/learn.txt 2>&1
  fi
done
echo "CAP LOOP DONE" >> $LOG
