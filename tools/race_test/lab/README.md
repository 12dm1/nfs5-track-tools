# Racing-line lab (AI3/AI4, 2026-10-14)

Research scripts kept as a record of how the converted-track AI was tuned. They run from Git Bash
(Windows) or bash, read the folders from `nfs5tools.ini` through `env.sh` and work in the game folder.
Paths with spaces are not supported. The maintained tools are `tools/race_test/*.py` and the AI6
pipeline in `tools/track_editor/ai_*.py`.

All runs: 19 converted tracks, 2 timed laps, 3 opponents, x4, one batch of 19 games at a time
(never more than 20 games at once). Results land in `<work>/reports/race_test/line_<NAME>/s<seed>/`.

* `variant.sh NAME [apply_lsp.py args]` - optionally rewrite the lines with `apply_lsp.py`
  (saved to `<work>/backups/lsp-NAME`), then race seeds 100 and 200 (`SEEDS="..."` to change).
* `learn_chain.sh BASE N [learn_line.py args]` - restore `lsp-BASE`, then N times: learn from
  the last variant's batches (`learn_line.py`), save `lsp-BASE<L>i`, race.
* `python summary.py VAR [VAR...]` - walls, car contacts, stalls, unfinished cars and bot time
  per variant over its seeds (first VAR is the reference).

See `docs/pipeline/ai.md` for the results.

## AI4-AI5 scripts (2026-10-14, evening)

One seed, one timed lap, all 19 tracks per batch: the races are deterministic, one run shows every
repeatable contact.

* `fast_line_loop.sh N` - slow mode (every `<track>.aispeed` = `0 99999 2.0`), N x `learn_line.py
  --kinds wall,hit3`; this made the forward lines FAST4.
* `zone_all_loop.sh N` / `zone_tune_loop.sh N` - speed zone factors (`../learn_zones.py`).
* `cap_loop.sh N` - speed caps (`../learn_caps.py`); `time_back_loop.sh` + `../revert_zones.py` -
  give lap time back where the caps protect.
* `rev_pipeline.sh base|line N|zones N|caps N|check NAME` - the same for reverse races
  (`<track>1.lsp`, `<track>_r.aispeed`).
* `check_cars.sh NAME SEED MODEL:SPEC:AISPEC...` - other cars (`EXTRA=` passes race_test options,
  `TRACKS=` other tracks); `stuck_check.sh`, `stuck_ab.sh` - AI5 stuck reset.
* `zone_stats_all.py BATCH LABEL` - one line of totals (walls, objects, spins, stalls, bot time).
