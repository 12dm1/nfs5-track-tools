#!/bin/bash
# slow_line_loop.sh N [SEEDS] : AI4b - learn the racing lines with the AI slowed down.
# All 19 converted tracks get a temporary GameData/Track/<t>.aispeed "0 99999 2.0" (the AI
# drives slowly, so where it still hits the track the line itself is to blame), then N times:
# race (one batch of 19 games per seed), move the forward line away from every wall and
# object contact (learn_line.py --kinds wall,hit3), save $WORK/backups/lsp-SLOW<i>.
# The temporary .aispeed files are removed at the end.
N=$1; SEEDS=${2:-"100 200"}
. "$(dirname "$0")/env.sh"
R=$WORK/reports/race_test/slow_line
LOG=$R/progress.txt
mkdir -p $R
TR="aquatica atlantic dolphin country empire france germany gt1 gt2 gt3 hills hometown lostcany park redrock rockypas snowy summit uk"
for t in $TR; do printf '# AI4b temporary slow mode\n0 99999 2.0\n' > GameData/Track/$t.aispeed; done
for i in $(seq 0 $N); do
  BS=""
  for SEED in $SEEDS; do
    python -u $TOOLS/race_test/race_test.py --track $TR --car 993 --opponents 3 --runs 1 --parallel 19 --laps 2 --seconds 900 --time-scale 4 --seed $SEED --load-timeout 300 --hang-seconds 60 --stagger 2 --out $R/it$i/s$SEED > /dev/null 2>&1
    B=$(ls -d $R/it$i/s$SEED/*_batch | tail -1); BS="$BS $B"
    python $TOOLS/race_test/lab/zone_stats_all.py $B "it$i s$SEED" >> $LOG
  done
  if [ $i -lt $N ]; then
    (cd $TOOLS/track && PYTHONIOENCODING=utf-8 python learn_line.py --game "$GAME" --kinds wall,hit3 --gain 0.3 --max-step 2.0 $BS) > $R/it$i/learn.txt 2>&1
    mkdir -p $WORK/backups/lsp-SLOW$((i+1)) && cp GameData/Track/racer/work/*[01].lsp $WORK/backups/lsp-SLOW$((i+1))/ 2>/dev/null
  fi
done
for t in $TR; do rm -f GameData/Track/$t.aispeed; done
echo "SLOW LINE LOOP DONE" >> $LOG
