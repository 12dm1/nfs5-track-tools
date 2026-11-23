#!/bin/bash
# zone_loop.sh TRACK ITERS [SEEDS] : learn AI speed zones for one track.
# Each iteration races SEEDS (default 16) games of TRACK (2 timed laps, <= 20 games at once),
# summarises the trouble, then learn_zones.py raises the zones at the hot spots.
TRACK=$1; ITERS=$2; SEEDS=${3:-16}
. "$(dirname "$0")/env.sh"
R=$WORK/reports/race_test/zones_$TRACK
LOG=$R/progress.txt
mkdir -p $R
for i in $(seq 0 $ITERS); do
  python -u $TOOLS/race_test/race_test.py --track $TRACK --car 993 --opponents 3 --runs $SEEDS --parallel $SEEDS --laps 1 --seconds 400 --time-scale 4 --seed 100 --load-timeout 300 --hang-seconds 60 --stagger 1 --out $R/it$i > /dev/null 2>&1
  B=$(ls -d $R/it$i/*_batch | tail -1)
  cp GameData/Track/$TRACK.aispeed $R/it$i/zones.aispeed 2>/dev/null
  python $TOOLS/race_test/lab/zone_stats.py $B "it$i" >> $LOG
  [ $i -lt $ITERS ] && python $TOOLS/race_test/learn_zones.py --game "$GAME" --track $TRACK $B >> $LOG 2>&1
done
echo "ZONE LOOP $TRACK DONE" >> $LOG
