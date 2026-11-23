#!/bin/bash
# thr550.sh : throttle cap while sliding (slip_limit_deg 12, slip_throttle 64): 550 without and with the ref table.
. "$(dirname "$0")/env.sh"
rm -rf $WORK/reports/race_test/cars_thr $WORK/reports/race_test/cars_thrref
EXTRA="--ini ai.slip_limit_deg=12 --ini ai.slip_throttle=64" bash $TOOLS/race_test/lab/check_cars.sh thr 100 550:550Arace:550a
EXTRA="--ini ai.slip_limit_deg=12 --ini ai.slip_throttle=64 --ini ai.ref_ai_spec=default" bash $TOOLS/race_test/lab/check_cars.sh thrref 100 550:550Arace:550a
