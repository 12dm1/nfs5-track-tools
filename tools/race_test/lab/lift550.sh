#!/bin/bash
# lift550.sh : 550 Spyder on 19 tracks, (A) EA lift fix only, (B) lift fix + ref table + slip limit.
. "$(dirname "$0")/env.sh"
rm -rf $WORK/reports/race_test/cars_lift $WORK/reports/race_test/cars_liftall
EXTRA="--ini ai.fix_full_lock_lift=1" bash $TOOLS/race_test/lab/check_cars.sh lift 100 550:550Arace:550a
EXTRA="--ini ai.fix_full_lock_lift=1 --ini ai.ref_ai_spec=default --ini ai.slip_limit_deg=12" bash $TOOLS/race_test/lab/check_cars.sh liftall 100 550:550Arace:550a
