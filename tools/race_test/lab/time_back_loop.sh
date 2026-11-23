#!/bin/bash
# time_back_loop.sh N [DECAY] [FIRST_SEED] : win the lap time back. Before each batch the bend
# severity factors give back (1 - DECAY) of their excess wherever the last two batches had no
# trouble (learn_zones.py --step 0 --decay); after it, caps go down where cars still crash
# (learn_caps.py). One batch (19 tracks, 1 lap) per iteration, seeds alternating.
N=$1; DECAY=${2:-0.5}; S0=${3:-200}
. "$(dirname "$0")/env.sh"
R=$WORK/reports/race_test/time_back
LOG=$R/progress.txt; mkdir -p $R
TR="aquatica atlantic dolphin country empire france germany gt1 gt2 gt3 hills hometown lostcany park redrock rockypas snowy summit uk"
CUR="$INIT_CUR"; PREV="$INIT_PREV"
for i in $(seq 0 $N); do
  for t in $TR; do python $TOOLS/race_test/learn_zones.py --game "$GAME" --track $t --min-events 1 --step 0 --decay $DECAY --history $PREV -- $CUR; done > $R/decay$i.txt 2>&1
  mkdir -p $R/it$i/zones; cp GameData/Track/*.aispeed $R/it$i/zones/
  SEED=$(( i % 2 == 0 ? S0 : 300 - S0 ))
  python -u $TOOLS/race_test/race_test.py --track $TR --car 993 --opponents 3 --runs 1 --parallel 19 --laps 1 --seconds 600 --time-scale 4 --seed $SEED --load-timeout 300 --hang-seconds 60 --stagger 1 --out $R/it$i > /dev/null 2>&1
  B=$(ls -d $R/it$i/*_batch | tail -1)
  python $TOOLS/race_test/lab/zone_stats_all.py $B "it$i s$SEED decay $DECAY" >> $LOG
  [ $i -lt $N ] && for t in $TR; do python $TOOLS/race_test/learn_caps.py --game "$GAME" --track $t $B; done > $R/it$i/caps.txt 2>&1
  PREV=$CUR; CUR=$B
done
echo "TIME BACK LOOP DONE" >> $LOG
