# AUTO1 — automated race runs (no frontend, AI drives the player car)

2026-10-14. Tool: `tools/race_test/race_test.py`.

## The game's own dev path

- `FUN_004b6660` loads `fe.txt` (the frontend -> race hand-off) into the "FE Data Stream",
  then `FUN_004b60d0(argc, argv)` feeds every command-line `KEY=VALUE` through the same
  parser (`FUN_004b5470`). Command-line keys therefore override `fe.txt`; `fe.txt` is never written.
- Key table at `005D1E40` = `{id, variable, name}` triples, filled at start-up
  (`mov [tbl], imm` run around `004B2000..004B4B80`). Global keys (`FE`, `NOINTROMOVIES`,
  `TRACK_NAME`, `NUM_LAPS`, `TRAFFIC_DENSITY`, `WEATHER`, `TIME`, `COPS`, `RAND_SEED`...) and
  per-car keys `car[N].xxx` (ids >= 0x21E) stored at `var + N*0x580`.
- `fe=0` (`FE` -> `0065B298`; `0065B910` is PRELOAD): no frontend, the race described
  by fe.txt + arguments starts directly (about 15 s to the green light).
- `car[0].aidrive=1` (`.AIDRIVE`, id 0x239, carInfo `0065808C` + 0x144): the AI drives the
  player's car. Verified: Coastal, 45 s, 1432 m, 191 km/h; without it the car stands still.

## Memory the script reads (PORSCHE.EXE 393C2E8F)

| Address | Meaning |
|---|---|
| `005E8B34[8]` | car objects, index 0 = player |
| car `+008` int16 / `+330` float3 / `+33C` float3 | slice / position (m) / velocity (m/s) |
| `00606AC4` | simulation tick (64 per second); 0 while the track loads |
| `00658080/84/88` | numcars / player race cars / opponent race cars |
| `0065808C + i*0x580` | carInfo: `+144` aidrive, `+228` filename |

The car objects exist while the track is still loading (tick 0, frame 1), so "race
started" = player car present **and** tick > 0.

## Usage

```
python tools/race_test/race_test.py --track coastal aquatica --car 993 959 \
    --opponents 2 --seconds 60 [--set weather=1 --set traffic_density=2] [--no-aidrive]
```

Several `--track` / `--car` values run as a matrix. The game takes the screen while it runs;
clicking elsewhere pauses the simulation (reported as a warning). Output per run in
`<work>/reports/race_test/<stamp>_<track>_<car>/`: `summary.json` (verdict, setup read back from
memory, per-car distance / top speed / longest stop, fps, sim rate, crash counts),
`samples.csv` (every car at 4 Hz), `log_excerpt.txt`; `dx11z.log` is copied on FAIL or with `--keep-log`.

Verdict: **FAIL** = no race within the load timeout, hang (no frames), game exit, unhandled
exception (`=== CRASH UEF`), wrong track/car/opponent count, player car never moved with aidrive.
**WARN** = an AI car never moved or stood >= 15 s, simulation paused, fps < 30, suspicious
wrapper log lines (`NOT installed`, `R4 NODE CORRUPT`...). First-chance `CRASH VEH` that the game
survives is only a note. Exit code 1 if any run failed.

Car and track names are checked against `GameData/CarModel|Track/<name>.crp` before launch:
an unknown `car[N].filename` is accepted by the game silently.

## Limits

- Frontend logic is bypassed: OS1 similar opponents, career tuning, network, MUS1 are not covered.
- Visual quality still needs a human; the script only measures.

## First finding

Every run logs `N1 race modes: ... text 0000 ... -> NOT installed`: `ExtendedContent_Install`
(HS8, `thrash_exports.cpp:514`) repoints every `call 004E06D0` (GetString) to its own hook
before `RaceModes_Install` (`:544`) runs, so `race_modes.cpp` `PatchCall` finds an unexpected
target at `004BB75B/004BB77B/0050C008/004BA27B` and the whole N1 race-modes feature stays off
while extended content is on. Not fixed here.

## Track crash fix (2026-10-14)

Matrix of all 19 HS tracks (40 s, 2 opponents): 16 drove, **germany, gt2, uk crashed while
loading** at `00554D7E`. `FUN_00535bc0` builds a texture object through `FUN_00554960`, which
returns NULL for a texture that is **not square or not a power of two**; the next call
dereferences it. Offenders: germany `0015` 33x32 + `0031` 16x32, gt2 `0097` 48x48, uk `0048` 64x32.
Fix: `nfs5_fsh.ensure_square_pot` (stretch to the next power-of-two square; CRP UVs are
normalised) in the converter, `verify()` now rejects such entries, and
`python nfs5_fsh.py --fix-sizes <fsh>...` repaired the three installed archives in place
(byte-exact round-trip checked first; originals in `<work>/backups/*.fsh.before-texsize`).
After the fix all three drive 60 s with opponents. Full-lap / finish runs were not done.

## Parallel runs, race trace, time scale (2026-10-14)

`race_test.py` v2: `--runs N --parallel K --laps L --time-scale S --seed X`.

* **Several games at once.** Each game starts suspended; two code bytes are patched in its
  memory only (PORSCHE.EXE on disk untouched): `0056562B 75->EB` (the single-instance mutex
  check at `005655F0`: CreateMutexA + ERROR_ALREADY_EXISTS -> activate the other window and
  quit) and `004B0F02 74 1F -> 74 60` (WM_ACTIVATE inactive in `FUN_004b0ee0` sets
  `006573E0/E1`; `FUN_004b0fa0` then spins until focus returns = the race pauses).
* **Wrapper (same DLL for normal play, nothing changes without these):** env `DX11Z_OUT`
  = directory for every file the wrapper writes, `DX11Z_INI` = INI to read
  (`log.cpp WrapperOutPath/WrapperIniPath`); `[autotest] time_scale` multiplies the
  game's GetTickCount (PERF4 1 ms counter). The scheduler `00564EB0` re-arms its
  timeSetEvent from GetTickCount and catches up with 1 ms events, so the fixed 64 Hz
  simulation runs `scale` times faster as far as the machine allows.
* **Race trace** `race_trace.cpp`, `[autotest] trace=1`: hooks the single call
  `0041041E call 0040F740` (cdecl(car), the per-car tick that consumes the impact the
  collision code left: car+430 magnitude, +438 class, +440 contact point; car+540 counts
  stopped ticks, respawn `FUN_0040f540` after 320). Writes `dx11z_trace.csv`:
  `S` rows every 4 ticks per car, `H` per impact, `R` per respawn (move > 20 m in a tick).
  Impact kind = class high word: **4 = wall**, **5 = another car**, **3 = unknown yet**
  (low-speed contact, piles up at Hometown slices 170-179 and after the finish).
* **Finish:** with fe=0 the game closes itself (exit code 0) when the player finishes,
  before the bots do; the script gives the game one lap more than `--laps` and stops the
  run when every car has done `--laps`. A lap counts only after the car was in the middle
  of the track (cars on the grid behind the line cross it at once).
* **Measured:** 4 games at x3 = 3.0x each; 8 games at x4 = 1.9x each (~15 race seconds
  per real second; the machine is the limit). Gt1 lap ~122 s (player and bots within 6 s).
* **Deterministic:** two games with the same `rand_seed` (`--set rand_seed=300`) produced
  byte-identical traces and identical race times, even in parallel at x3. A/B of a fix =
  same seeds before and after.
* Batch output: `reports/race_test/<stamp>_batch/report.md` (per track: race times of player
  and bots, impacts per car, kinds, respawns, stops, and the slice bins where events pile
  up across runs) + per run `summary.json`, `events.json`, `dx11z_trace.csv`, `dx11z_phys.log`.
* Known: `depth_smoke` "HS11 hard world cutout selected" fails (render-state code, not touched).

## Baseline of the 19 HS tracks and two fixes (2026-10-14)

Batch `reports/race_test/20261014-133620_batch` (19 games at once, x4, 3 opponents, 2 timed
laps, seed 100): **0 crashes**. Ranking of the track spots by estimated seconds lost is in its
`report.md`; worst: france 860-869 (all 4 cars stall, 612 s), summit 1530-1539, aquatica
1010-1019, lostcany 280-289, country 440-449, redrock 620-629 (10 respawns), snowy 850-859
(player pinned on a wall in a dead end 141 s, 4300 wall contacts, the game's stopped counter
car+540 stayed 0 so the stock 5 s respawn never fired). Gt1 is the clean reference
(12 contact episodes per car, no stalls).

Fixed on the way:
* **Smackable limit.** Cars and smackables share one object list indexed `& 0x7F`
  (`FUN_004a1280`: table[(index & 0x7F) - numcars], called from car creation `FUN_00410730`
  for objects created by `FUN_00411130` = numcars + NUMSMACKABLES `0065807C`), and the game
  takes at most 125 smackables. Empire/Lost Canyons/Redrock had 127/176/188 placed SIGNs (HS12
  converts every collision_type=2 object) -> 125 + 4 cars = crash while loading any race with
  4+ cars. `nfs4_geometry.MAX_DESTRUCTIBLE = 119` (128 - 9 cars with traffic): the nearest to
  the road stay, the rest are not converted at all (user decision). `convert_track.py --crp-only`
  rebuilds only the CRP (verified byte-identical on Country). Installed; backups
  `<work>/backups/*.crp.before-smkcap`.
* **AppCompat shim crash.** The registry layer of PORSCHE.EXE is
  `IgnoreFreeLibrary<dx11z> HIGHDPIAWARE`; its AcGenral hook crashed on the NVIDIA driver thread
  (AcGenral+B5246 reading 0x48) in parallel runs. race_test sets `__COMPAT_LAYER=HIGHDPIAWARE`
  for its games only; the user's registry is unchanged (normal play may still hit it).

Report details: contacts within 0.5 s are one episode; a stall = no progress of more than 3
slices for 8 s or more; after a car's game finish (laps + 1) its data is cut.
