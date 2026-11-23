# AI6 step 1: "AI line" view in the track editor (2026-10-18)

Read-only diagnostics for the AI on converted (and stock) tracks, inside the atmosphere editor.

    run_ai_line.bat [track]                  (= run_track_editor.bat with --ai)
    python tools/track_editor GameData/Track hometown --ai
    python tools/track_editor GameData/Track hometown --ai-selftest      (offscreen, numbers)
    python tools/track_editor GameData/Track hometown --ai-ui-selftest   (hidden window, all panels)

Code: `tools/track_editor/ai_data.py` (loading), `ai_render.py` (GL overlay), `ai_ui.py` (panels);
mode switch "AI line" in `ui.py`, camera focus + overlay draw + selftests in `__main__.py`.

## What it shows

3D overlay (flat ribbons on the road, translucent wall panels, event posts; "On top" ignores depth):

* centre line = NFS4 vroad (`tr.frd`), optionally coloured by the `.aispeed` zone factor or speed cap
  of the race direction; lane edges;
* walls the wrapper collides with: `GameData/Track/<track>.hsw` corridor edges (red) and HSX1 object
  edges (orange);
* racing lines `racer/work/<track>0.lsp` (yellow, forward) and `<track>1.lsp` (cyan, reverse);
* trajectories of the cars of a race_test trace (`dx11z_trace.csv`, S rows every 4 ticks), coloured
  by speed, speed minus the AI target (`car+EB8`), pedals, steering (magenta = full lock) or slip;
* events: wall / object / car contacts (race_test episodes from `events.json`), stalls, respawns and
  spins (slip > 60 deg above 30 km/h, as `learn_zones.spins`).

Panels:

* **AI**: track data summary, layers, trace picker (background scan of `reports/race_test` for
  `NNN_<track>_*` run dirs up to 4 levels deep, newest first, auto-loads the newest), path field for
  any trace, colour mode, lap filter (lap 0 = grid to the line), cars (checkbox = drawn, radio = plotted)
  with lap times and event counts.
* **AI events**: table, click = camera to the spot (top view with driving direction up, or chase view).
* **AI plots**: speed (car, AI target, other cars, `.aispeed` caps and factors x100, brake marks, event
  markers) and lateral offset (line fwd/rev, nearest `.hsw` wall each side, lane, car) along the slices;
  linked X axes; click = camera to that slice (Shift = top view), the yellow line drags the camera.

Checks (`--ai-selftest`): vroad centre points lie on the drawn road (hometown: nearest track vertex
2.1 m median, height diff 0.08 m), lines stay inside the walls, overlay draws (28-48 % of pixels).
Hometown, summit, gt1 and the stock monaco1 (no vroad: lines + traces only, heights from the CRP
article centres) pass. The plain `--selftest` is alps-specific (snow at slice 900) and fails on
hometown with or without this change.

## First numbers (993, cars_final2 batch of 2026-10-14, bots, laps >= 1)

| | converted HS tracks | stock monaco1-4 |
|---|---|---|
| time on the brake | 37-47 % (mostly light, < 64/255) | 15-29 % |
| time at full throttle (> 200) | 41-55 % | 65-85 % |
| mean speed - AI target | 29-51 km/h | 117-144 km/h |

On stock tracks the planner's target sits far above the car's speed most of the time. On converted
tracks the car is held near the target and brakes lightly very often, so the speed planner (and
our caps on top of it) limits the bots almost everywhere. Caps bind 7-38 % of the time, zone
factors > 1 cover 20-60 % of the slices. A heading-noise check of the vroad forward vectors (1/128
quantised) found real noise on straights only on Hometown (1.8 deg/slice raw vs < 0.5 smoothed);
on the other tracks the per-slice heading change is the real curvature. Step 2 (own speed profile)
should start from these plots.

## A line from a lap the player drove (2026-10-18)

    run_record_line.bat                                   drive, quit; the session is split per race
    python -m tools.track_editor.ai_record make <track> [--rec DIR] [--lap N] [--smooth 1] [--margin 1.2] [--dry]
    python -m tools.track_editor.ai_record restore <track> [--dir 0|1]

`run_record_line.bat` starts the game with `dx11z_record.ini` (= `dx11z.ini` + `[autotest] trace=1
trace_every=2`, rebuilt on every start, via `DX11Z_INI`), waits for it to exit and calls
`ai_record collect`, which writes every race of the session longer than 2 s to
`<work>/reports/line_record/<yyyymmdd-hhmmss>_<track>_<race>/dx11z_trace.csv`. The AI view lists
these as `driven: ...` next to the race_test runs.

`make` takes the newest recording of the track, car 0, and the last completed lap without a
respawn (a lap is complete when another wrap follows, or when it covers 97 % of the ring and ends
at the line). Lap 0 is grid -> line, as in race_test. Per slice: the point where the car crossed
the plane through the vroad point across the driving direction (trace rows +-10 slices around it,
linear interpolation, teleports skipped), lateral offset, Gaussian smoothing (sigma 1 slice),
clamped `--margin` m inside the nearest .hsw edges on both sides of the line (corridor + objects,
as `apply_lsp.clear_line` but without its islands logic), written to `racer/work/<track>0.lsp`
(forward race) or `1.lsp` (reverse). First install keeps `.lsp.orig`, each install `.lsp.bak`.
It prints the lap, contacts in it, gaps, clamped slices and the difference to the installed line.
Checked on race_test traces (bot-driven car 0) of hometown, summit, gt1 with `--dry`.
Converted tracks only (needs the vroad).

## Step 2: own speed profile (2026-10-18, in progress)

Wrapper (`ai_tuning.cpp`, test build a test build of the PC wrapper as `Drivers/dx11ai6z.dll`, used by
race_test through `RACE_TEST_DRIVER=Drivers\dx11ai6`; the installed `dx11z.dll` is untouched):
`[ai] speed_profile=1` (needs `speed_zones=1`, the hook at 0040BAEA) loads
`GameData/Track/<track>[_r][.<profile_tag>].aiprofile` ("slice kmh" lines). On converted tracks a
racer's (car+52C & 1, not traffic/cop/smackable) target speed +EB8 = profile[slice] x
`profile_scale`, before SlipLimit; RefClamp is skipped for it; the track's .aispeed (zones and caps)
is not loaded when a profile is.

`ai_speed.py make <track> [--lat 10 --brake 4.5 --lead 0.5 --tag X]`: curvature of the .lsp
(circle through i+-2, smoothed), corner speed sqrt(lat/k), braking pass backwards along the driving
direction, lead = minimum over the next `lead` seconds. `# fix a b factor` lines are kept on
regeneration. `ai_speed.py calibrate` = v^2 k the cars reached.

`ai_tune.py check <track> RUN` (target follows the profile?, incidents) and `loop` (parallel races
per --lat with their own tagged profiles, cuts x0.94 over 15 slices before each hard incident).

First sweep (summit, 993, seed 100, lat 10 brake 7 lead 0.4, profile_scale 1.0-1.3): the target
follows the profile 75-99 % of the time (the rest is SlipLimit). Bots 303 / 290 / 285-306 /
276-298 s per lap against 300 s with the planner, but wall contacts 0 / 9 / 21 / 25. Before the
incidents the AI brakes with at most 120-160 of 255 and lands 20-50 km/h above the target: it
decelerates ~4-5 m/s2, not 7, so brake = 4.5, lead = 0.5. profile_scale also scales the braking
(v^2), so sweeps use tagged files per lat instead.

Summit loop 4 (brake 3.5, crest 1.2, 2 seeds x 4 cars, iteration 0): lat 16 lead 0.5 / 1 / 1.5 / 2
= 280.8 / 280.1 / 285.9 / 288.5 s with 17 / 4 / 4 / 1 hard incidents (planner ~300 s, ~4 impacts
per car). Lead 1 s removes most of the spins at the 887 hairpin (the brake controller is
proportional: the car runs ~24 km/h over the target while braking, so it must start earlier).

## Batch over all converted tracks (2026-10-19)

The player recorded one forward lap per track (`run_record_line.bat`; signs and fences knocked
over are fine, H class 5 = smackables and cars, not counted as incidents).

* `[ai] line_tag=X` (wrapper): when the race starts (LoadZones), `racer/work/<track><0|1>.X.lsp`
  is copied over the loaded line resource ([005E671C]+0x58), so several lines of one track race
  at the same time. Verified on summit seed 100: tag = a copy of the installed line gives a
  byte-identical trace; tag `old` makes the bots follow the shipped line (median 1.4 m off it vs
  2.2 m off the recorded one).
* `ai_record make --dir 0 --tag rec`: newest recording of that direction with a usable lap.
* `ai_batch.py run`: one launcher (FIFO, >= 7 s between game starts, <= 60 games, only while
  >= 3 GB RAM is free) and one pipeline thread per track: stage 1 old/rec x lat 13/16/19
  (lead 1), stage 2 best line + per-sector mix (40-slice sectors, the other line where its time +
  4 s per incident is lower, 12-slice blends) x two lats x lead 1/1.5 with the cuts carried on,
  confirmation of the two best profiles and the old AI on fresh seeds, then GT1race and
  944coupe25 physics. Results under `<work>/reports/race_test/ai6_batch/<stamp>/<track>/`
  (log.txt, history.json); `<track>0.final.lsp` + `<track>.final.aiprofile` are the picks;
  `ai_batch install` writes them over the real files.

### Batch 20261019-004042 results (default.sim/default.ais, 4 cars x 4 fresh seeds per track)

2280 races in 4 h 25 min (7 s between launches, 13-17 games at a time). The player's line was
faster than the shipped one on 18 of 19 tracks in stage 1 (empire 176 vs 222 s, gt2 109 vs 125 s);
the mixes take the shipped line on 1-5 sectors (summit: the 887 hairpin and 910-919; atlantic the
walls at 249 and 610-619; hometown keeps the shipped line except the player's sectors around the
spot where the player car got stuck at 553). Picks: lat 17.5-22, lead 1-1.5, 1-29 fixes.
Full table: `<work>/reports/race_test/ai6_batch/20261019-004042/report.md`.

Bots' median lap -10.8 % against the old AI (planner + .aispeed, shipped line), hard incidents
108 -> 84 over all tracks. `ai_batch refine` (3 more cut iterations on new seeds, penalty 10 s)
then lowered france/uk/germany/redrock/snowy/dolphin to 0-2 incidents per 16 laps (redrock
16 -> 1, dolphin 10 -> 0) at +-2 s; atlantic and aquatica kept their batch profiles.

### Other cars: aiprofile_cars.txt

The profiles are absolute km/h for the default physics. With them unscaled the 944 hit walls
30-148 times per track (8 laps) and the GT1 0-14: a 944 enters gt2's 236 at 140 km/h where the
old AI drives 91-100. The wrapper (`LoadCarScales`/`CarScaleOf`) finds each racer's .ais by
comparing its AI record (car+764, the file data; +0x80 the speed table) with
AICarData/*.ais and multiplies the profile by its line in GameData/Track/aiprofile_cars.txt
(`* x` = cars without a line). `ai_cars.py calibrate` raced every .ais with its own .sim x
profile_scale 0.6-1.0 on gt2/uk/france/rockypas/summit (4 cars, 1 seed): the scale taken is
the largest with <= 7 hard incidents per 20 laps (the old AI's rate with the default car).
Result: 356 family 0.66, 914/928/Gt2/Gt3 0.78, 550a/930t/944/964/965t/993/996t/Gt1 0.85,
935/959/993t/996/boxster 0.92, Gt4 1. CarreraGT (its .sim + model crash at load) and 901 (0.78
still 11 incidents) are not measured.

Identical .ais files: 918 = CarreraGT = Gt1 = Gt4, 356A = 356B, 901 = 930t, 928 = 965t (same 304
bytes). The wrapper cannot tell them apart by the AI record, so such a car takes the lowest
scale of its group (GT1/GT4/918/CarreraGT 0.85, 901/930t 0.72, 928/965t 0.78). The first check
run (check_20261019-063608) still had GT1 at x0.92 (it matched 918, unlisted) - superseded.

### Multi-car refine, final check, install (2026-10-19 morning)

`ai_batch refine` with the default car + GT1 + 944 (per-car scales on, cuts from all three) on
aquatica/country/atlantic/gt2/empire/redrock/rockypas: new profiles kept for gt2, atlantic,
empire, rockypas. Final check (`check_20261019-071346`, seeds 300/301, 4 cars, vs the old AI on
the shipped line): 944 x0.85 -6.3 % lap time (faster on 17 of 19 tracks), hard incidents 40 -> 35;
GT1 x0.85 +8.0 % (faster on 1 of 19), 33 -> 32. GT1 x0.9 / x0.95: +2.2 % / -2.9 % lap time but
0.33 / 0.39 hard incidents per lap (old AI 0.22). GT1 on the planner + the old .aispeed over the new
line (car scale 0, wrapper supports it): same lap times as the old AI, 0.42 per lap.

Why the GT1 loses: the profile is shaped for the default car and its crest limit is too careful on
small bumps (vertical curvature over +-2 slices: hometown 200 kv -0.013 -> 109 km/h, with lead
1.5 s 71 km/h from slice 194, where the old AI's GT1 does 178). Next: crest curvature over a longer
span (or only real jumps), then per-class profiles (fast cars tuned with their own physics).

Installed (dev machine install script with rollback):
Drivers/dx11z.dll = build-ai6 c0df438193d2 (before: f0e10fd15ab8, kept as
the previous dx11z.dll; same sources plus the AI6 changes in ai_tuning.cpp/.h and
thrash_exports.cpp), dx11z.ini [ai] speed_profile=1, racer/work/<track>0.lsp = the final lines
(shipped ones in .lsp.orig), GameData/Track/<track>.aiprofile, aiprofile_cars.txt. Smoke races on
the installed setup (gt2 + summit default car, gt2 944): PASS, the log shows the profile, the
per-car match (default x1.00, 944 x0.85) and the .aispeed loaded for unprofiled cars. Reverse
races still use the planner + <track>_r.aispeed (no _r.aiprofile).

## Round 2 (2026-10-19 day): crests, bunched slices, crashes, minimized test games

User: lap time first - light wall/car contacts are fine when the lap is faster, only real crashes
count. `ai_tune.crashes`: rollover (up_y < 0.5), spin, stall, respawn, or a wall/object contact
followed by >= 40 km/h and >= 30 % speed loss in 1.5 s ("heavy"). `ai_batch.evaluate` (and so
every score and every cut) uses only these; atlantic GT1: 7 old "hard incidents" = 1 crash.

Crests: the vertical curvature over +-2 slices took 1-2 slice bumps for jumps (hometown 198:
102 km/h, the old AI's GT1 does 178 there; the crest limit cost 0.2-5 % of the ideal lap).
`ai_speed` `vspan` (slices) and `crest` (0 = off); `ai_batch.retune` raced c12v2 (as before),
c25v3 (crest 2.5 over +-3) and c0 (off) x 4 cut iterations with the default car, GT1 and 944,
then confirmed the best against the current one on 6 fresh runs: 15 of 19 tracks changed
(c0: country germany hills hometown park summit uk; c25v3: empire france gt1 gt2 gt3 lostcany
redrock rockypas; kept: aquatica atlantic dolphin snowy), e.g. france 141.9 -> 137.6 s median,
crashes 11 -> 3; hills 136.6 -> 132.8.

Bunched slices (user: empire ~65 %, the grass cut of the 90-degree left - bots crawled on a
straight): where slices fan out on the inside of a corner, the line's plane crossings land
1 m apart and out of order (empire 862-866 headings -21 76 107 166 -47 while the player drove
-21 .. 0). Two fixes: `ai_speed` `arc` = curvature also by distance (+-10 m along the line),
the profile takes the smaller of both (empire 866: 25 -> 115 km/h, ideal lap -5 s, the others
unchanged within 1 s); `ai_record.untangle` replaces points that turn > 60 degrees a step, or
> 35 degrees where the road does not turn, by a straight run (empire 861-866, uk 97-100,
atlantic 249-253 / 1069-1071; real 90-degree street corners turn the vroad too and stay:
hometown 557, country 592/990). `ai_record make` does it for new recordings. Bots on empire
860-866: 19 -> 75 km/h.

Test games minimized: wrapper `[display] start_minimized=1` (windowed mode: ShowWindow
SW_SHOWMINNOACTIVE after the window setup; nothing is presented while minimized, the race runs
on). race_test writes it by default (`--show` keeps the windows).

Cars: 918, CarreraGT, Gt4 (unfinished car additions) are left out of aiprofile_cars.txt and of
the calibration; identical .ais take the lowest *listed* scale of their group, so those cars
count as Gt1.

Recalibration with the crash rule (lap + 10 s per real crash): 356s 0.73, 550a/930t/944/964/Gt2/914
0.85, 901 0.79, 928/959/965t/993/996/Gt3/boxster 0.92, 993t/996t 0.99, Gt1 1.05 (the top of its
0.85-1.05 grid on all 19 tracks), 935 1.06. Final check (`check_20261019-112040`, seeds 300/301,
4 cars, vs the old AI on the shipped line, real crashes): default car -12.1 % lap time (faster on
19 of 19), crashes 46 -> 64; GT1 -13.4 % (19 of 19), 31 -> 55; 944 -7.6 % (17 of 19), 11 -> 17.
Rollovers 2 of 136 crashes; most are heavy hits at a few spots. `refine` (3 cars) then: atlantic
26 -> 7 crashes, rockypas 16 -> 3 (lap 226 -> 219 s), summit 33 -> 18, aquatica unchanged.

Summit jump: with crest 0 the take-off (305-311) is reached at 128-137 km/h (82 with c12v2) and
the landings 315-335 crash. A fix factor cannot slow a straight (vmax x f stays above the braking
curve), so `ai_speed` caps: `# cap a b kmh` lines, a hard limit before the braking pass.
summit `cap 296 312 95`: crashes 21 -> 11 (none at the jump), median 275.0 -> 272.3 s. Caps are
read from the file being written, so a tagged regeneration does not carry them over.

Installed again 2026-10-19 12:2x (install.py): Drivers/dx11z.dll b8490a80a01e (AI6 + start_minimized),
final lines (empire/uk/atlantic untangled) and profiles (arc 10, per-track crest model, summit
cap). Smoke on the installed setup (minimized): empire default 161-164 s, summit GT1 253-257 s
(old AI 298), gt2 944 123-125 s (old AI 144), all PASS, per-car match default 1.00 / Gt1 1.05 /
944 0.85.

## Round 3 (2026-10-19 afternoon): hometown, GT1 pace

User: in a real Quick Race on hometown (GT2, a calm 1:34) the bots (GT1, 935...) fell 1000 m
(~22 s) behind; GT1 must be under 1:30. Findings: (1) the installed hometown profile (lat 19,
brake 3.5, lead 1) has an ideal lap of 103.5 s - no bot can beat it; (2) half the tracks picked
lat 22, the top of the grid; (3) 4 of 7 bots had tuned cars whose AI record matched no .ais
(fallback x0.92); (4) SKILL 0/1/2 changes nothing for profiled bots (identical races).
The default car is power-limited (73 % of hometown below target at throttle ~248/255), ~110 s
whatever the profile.

Hometown sweep (3 s launches, no instance limit - user): lat 22/26/30 x brake 3.5/6/9 x lead
1/0.6/0.3, GT1 + default, cuts: GT1 best lat 30 brake 9 lead 1. Wrapper `[ai] profile_brake_gain`
/ `profile_throttle_boost` (target below/above the profile to make the proportional controller
brake/accelerate harder) made it worse (gain 2) - off. Per-track car scales in the profile
(`# carmul 0.85`, `# carscale Gt1 1.05`): the profile is tuned for the GT1, the other cars x0.85
on this track. Unmatched AI records take the nearest .ais (fewest differing bytes, < half).
Result (12 laps per car, fresh seeds): GT1 101.1 -> 88.4 s median (best 85.8), default 112.3 ->
109.9, 944 136.8 -> 133.9; installed 2026-10-19 ~14:00 (DLL 21dcf3bb52f4). Smoke, 8 GT1s: 85-88 s.

### Tuned bots, per-car scales on hometown (2026-10-19 evening)

The nearest-.ais match was unreliable (119 of 304 bytes differed, wrong cars). `CarScaleOf` now
reads the car's .ais name from the race setup (car index i in 005E8B34 -> `0x0065808C + i*0x580
+ 0x2A8`; +0x228 model, +0x268 .sim) and takes that .ais scale. A tuned bot (Quick Race uses the
career cars with parts: turbo kit, spoiler, lightening) gets its AI record rebuilt by the game;
the mean ratio of its speed table (record +0x80, 40 floats) to the stock .ais, divided by 1.035
(the ratio even untunable race cars show - 935/GT1/GT2/GT3 have no parts), clamped 1.0-1.2,
multiplies the scale. In a user race: 930T x1.127, 993 S x1.115, 965T x1.09, 996T x1.077.
Unverified: race_test cannot fit parts, so whether that gain suits the corners is not measured.
DLL 587e2522c6f5.

Hometown per-car scales (stock physics, `# carscale` in the profile): 935 1.1 (88.8 s), 993t 0.9
(103.2, crashes above), 996t 0.92 (103.7), 993 0.85 (110.7), 965t 0.78 (114.3), 930t 0.72 (124.2),
Gt1 1.05, the rest listed x carmul 0.85.

### The other 18 tracks the same way (g1all, 2026-10-19 evening)

Scratchpad script g1all.py, `reports/race_test/ai6_batch/g1all_<time>/`: S1 lat 22/26/30 x
brake 3.5/6/9 (lead 1, the track's crest/caps/fixes) + current, GT1 x 3 seeds; S2 3 cut
iterations (GT1 x2, default, 944); S3 absolute scale grid per car (Gt1, default = carmul, 944,
935, 993t, 996t, 993, 965t, 930t, Gt2, Gt3; 3 points + one step past an edge, 2 seeds); S4
current vs new, GT1/default/944 x 2 fresh seeds, new installed when the summed score is lower
(old kept as .pre_g1).

Result `g1all_20261019-173121` (2398 games, 17:31-19:39; S4 = 4 cars x 2 fresh seeds per car):
GT1 -9.1 % lap time vs the round-2 profiles (faster on 18 of 18, e.g. gt1 105.6 -> 93.4 s, gt2 91.4
-> 85.0, atlantic 168.1 -> 143.6), 944 -2.3 %, default car +1.1 % (power-limited, now x0.8-0.9);
crashes GT1 5 -> 9 (summit 5), default 17 -> 11, 944 9 -> 11. Sweep picks: mostly lat 26, brake
3.5-6; lat 30 on gt1/gt2/dolphin, lat 22 on germany/uk. Per-car scales: Gt1 1.05 nearly everywhere
(france 1.1, park 1.0), default 0.8-0.9, 944 0.78-0.84, 935 0.95-1.1, 993t 0.85-0.95, 996t
0.86-0.98, 993 0.85-0.9, 965t 0.78-0.9, 930t 0.72-0.84, Gt2 0.72-0.96 (0.96 = grid top on 9
tracks), Gt3 0.84-1.0. 16 tracks installed by the script; gt3 (one S4 game never started a race,
4 laps counted missing) and snowy (sum tie, GT1 -4.5 s) installed by hand. Old profiles:
<t>.aiprofile.pre_g1 / <t>.final.aiprofile.pre_g1.
