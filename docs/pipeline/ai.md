# AI on converted tracks

The stock PU AI drives a converted track from its racing line (`racer/work/<track>0/1.lsp`) and its speed planner.
Out of the box it is too fast into bends and hits walls: NFS4 slices are 4.6 m long, PU's 6.0 m, and the exe has
per-track zone tables only for stock tracks. This page is the workflow. The numbers and the reasoning are in
[AI1](../findings/AI1-converted-track-ai.md) and [AI6](../findings/AI6-ai-line-view.md); the file formats in
[../formats/ai-files.md](../formats/ai-files.md); what the runtime must hook in [../runtime.md](../runtime.md)
section 8.

## Use the tuned data (most people stop here)

`data/ai/` holds the result of the tuning for all 19 tracks: smoothed/recorded racing lines, forward and reverse
`.aispeed` zones and caps, `.aiprofile` speed profiles and the per-car scale table.

```
python tools/track/convert_track.py --all --install <game>
python tools/track/install_ai_data.py --install <game>
```

Result of the tuning: the speed lookahead fix took wall contact episodes from 2526 to 1101 and respawns from 23 to 2
(19 tracks, 76 cars); the zones then took wall hits from 511 to 52 and spins from 173 to 10 at a cost of +8.7 % bot
lap time (AI1); the AI6 lines and profiles won back the time and more: -10.8 % bot lap time over the 19 tracks.
Light car-to-car contacts are tolerated.

## Tune a track yourself

The loops need races. `tools/race_test/race_test.py` runs them unattended (no menus, AI driving the player car,
several games in parallel, per-tick traces); it is built for the PC wrapper and its INI/log, so with another runtime
you need an equivalent command line and trace. Rules learned the hard way: launch games at least 3 s apart, never
run two big batches at the same time, and count only real crashes (rollover, spin, stall, heavy hit) as incidents:
lap time comes first.

1. **Look.** `run_ai_line.bat <track>`: line, walls, lanes; load a race_test run to see where cars hit and how speed
   follows the target.
2. **Racing line.**
   * smooth / keep off walls: `python tools/track/apply_lsp.py --smooth 3 --obstacle-clearance 5 ...`;
   * learn it from contacts: `python tools/track/learn_line.py --kinds wall,hit3 --gain 0.5 --max-step 3 ...`;
   * or drive it yourself: record a lap with a trace (PC wrapper: `[autotest] trace=1`), then
     `python -m tools.track_editor.ai_record collect <trace.csv>` and
     `python -m tools.track_editor.ai_record make <track>` (keeps `.lsp.orig`/`.lsp.bak`; `restore` undoes it;
     `splice` replaces a slice range from another line).
3. **Speed.** Either zones + caps (`.aispeed`):
   `python tools/race_test/learn_zones.py ...`, `learn_caps.py ...`, `revert_zones.py ...` (each takes `--reverse`
   for `_r.aispeed`), or a profile (`.aiprofile`, replaces zones and caps):
   `python -m tools.track_editor.ai_speed make <track> [--lat 10 --brake 4.5 --lead 0.5 --tag X]`, checked with
   `python -m tools.track_editor.ai_tune check <track> RUN` and tightened by `ai_tune loop`.
4. **All tracks at once.** `python -m tools.track_editor.ai_batch run [--tracks ...]` runs the whole line + profile
   pipeline per track in parallel and writes `<track>0.final.lsp` + `<track>.final.aiprofile`;
   `ai_batch install` copies the picks over the real files.
5. **Other cars.** Profiles are absolute km/h for `default.sim`/`default.ais`.
   `python -m tools.track_editor.ai_cars calibrate` finds a scale per `.ais` and writes `aiprofile_cars.txt`.
6. Copy the results back into `data/ai/` if you want to keep or share them.

The research scripts that drove the AI1 loops (overnight batches, A/B variants) are in `tools/race_test/lab/`; see
its README. They are shell scripts around race_test and are kept as worked examples, not as a supported interface.

## What does not help (measured)

* Steering lookahead x0.6 or x1.4: +39 % / +53 % wall contacts.
* A `.edg` corridor built from the walls for the SimD off-track check: more respawns (reverted; the corridor is only
  widened on shortcuts, HZ5).
* Planning the target guard 20 slices ahead: obstacle hits doubled.
* A steering limit by speed: more spins.
