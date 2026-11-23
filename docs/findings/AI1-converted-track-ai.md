# AI1: why the AI hits walls on converted NFS4 tracks

2026-10-14. Measured with `tools/race_test/race_test.py` (19 HS tracks at once, x4, seed 100,
3 opponents, 2 timed laps; same seed = byte-identical race, so every comparison is exact).
Compare batches with `race_test.py --compare A B`.

## How PU's AI drives (PORSCHE.EXE 393C2E8F)

* **Steering** `FUN_0040bfe0` (per car tick): target = mean of racing-line points (`.lsp`,
  `FUN_00403450`) L..L+3 slices ahead, `L = max(4, |car+D60| (m/s) * [car+760]+140 * 4/21)`;
  `[car+760]+140` = 1.0 for every car seen, so L = 5-7 slices at race speed. A car ahead shifts
  the target sideways. `car+EB8` is the steering value, not a speed.
* **Speed** `FUN_0040b650`: looks `local_c` slices ahead (per-car table `[car+764]+0x20`,
  indexed by speed), takes the largest heading change of the SimD forward vectors there (left
  and right separately), times `[car+760]+144`, and looks the speed up in `[car+764]+0x80`.
  It does **not** read the `.key` file (rewriting every `.key` with 399 points changed nothing,
  byte for byte).
* **SimD walls** (+0x40/+0x44) feed a road-width factor (capped at 1.0) and the off-track respawn
  check `FUN_00408750`; narrowing them to the real walls changed nothing on 14 of 19 tracks and
  hurt on 3 (respawns) -> reverted, `nfs4_geometry.AI_CORRIDOR_FROM_WALLS = False`.
* **`.edg` barrier** is read by the AI (recovery / avoidance), not for the cornering line.

## The migration defect: slice length

PU's own tracks have **6.0 m** slices, the converted NFS4 tracks **4.6 m** (NFS4 vroad step).
Every AI distance counted in slices is 23% shorter on a converted track: the speed planner sees a
bend later and measures a smaller heading change, so it enters too fast and brakes too late.
Restoring PU's distance (`local_c * 6.0/4.6`, wrapper `[ai] speed_lookahead_scale=1.3`, hook
`0040B6FD`, converted tracks only):

| | baseline | + smoothed line | + speed lookahead x1.3 |
|---|---|---|---|
| wall contact episodes (19 tracks, 76 cars) | 2526 | 2487 | **1101** |
| car contact episodes | 696 | 525 | 483 |
| respawns | 23 | 17 | **2** |

## Other levers tried

* Steering lookahead (`[ai] lookahead_scale`, hook `0040C1D9`): x0.6 -> wall contacts +39%,
  x1.4 -> +53% (both with the old speed planning). Left at 1.0.
* Racing line smoothing (`tools/track/apply_lsp.py --smooth 3`): the NFS4 lines jump up to
  3.7 m between two slices (30-60 deg kinks). Smoothed: car contacts -20%, bots faster on most
  tracks, walls -2.5%. Installed together with the x1.3 test.
* `.edg` from the real walls (`tools/track/apply_corridor.py --edg`, `track_corridor.py`):
  long stalls 3216 -> 2528 s, player times much better on Hometown/Snowy/Country, worse on two
  tracks (chaotic stall spots). Installed.
* The player car on aidrive gets pinned on a wall for minutes (the stock 5 s respawn counter
  car+540 never starts for it); bots get out. Left for the end (user's order).

Backups of every replaced file: `<work>/backups/{crp,edg,key,lsp}-before-AI1/`.

## AI2-AI4 (2026-10-14, afternoon): walls, islands, the racing line

Measured on all 19 tracks, 2 timed laps, 3 opponents, x4; one batch of at most 19-20 games at
a time (user rule). Compare with `race_test.py --compare`.

**Race trace extended** (`race_trace.cpp`): S rows now also carry surface (car+464), up.y
(+374), forward x/z (+37C/+384), velocity x/z, throttle/brake bytes (+D9C/+D9D) and steering
(+DA0). Impact kind 3 comes from `FUN_00496610` <- `FUN_00498690` (body points against an object
or terrain face), not from walls.

**Target guard** (`ai_tuning.cpp`, `[ai] target_guard=1`, hook `0040C865 call 00532330`, the
`target - position` right before steering; esi = car): the car's swept path to its target
(centre and +-1.2 m) is tested against the `.hsw` edges; a blocked target moves sideways to the
nearest free spot that the car can still reach (`guard_max_accel` 9 m/s^2). Walls 1057 -> 801,
stalls halved. **On in the user's INI.**
* Plan-ahead variant (`guard_far_slices` 20, test the path to the line point 90 m ahead):
  obstacle hits doubled -> default 0.
* Steering limit (`steer_limit_speed`): spins 270 -> 329, off. 86% of the spins had full lock
  (DA0 +-126; steering = dot(target dir, car right) * 256) and 63% full lock plus hard braking at
  120-170 km/h; only 37% were off asphalt. Cause not fixed yet.

**Obstacles** (UK islands, Redrock / Lost Canyons pillar rows, Empire island) - found by
cross-sections of the `.hsw` (`apply_lsp.wall_crossings`): a thin wall with a walled lane of
5 m+ beyond it. `apply_lsp.py --obstacle-clearance 5`: the line keeps 5 m from them, the
correction ramps in/out at 0.3 m per slice (the authored line keeps its shape elsewhere).
Obstacle-zone hits (8+12 games): UK 61 -> 43, Redrock 13 -> 2, Lost Canyons 18 -> 0,
Empire 5 -> 3, no regression elsewhere. Clearance from ALL walls (2-3 m) changed nothing
useful; with the rate limit on the line itself it flattened every bend and hurt UK.
The UK island: cars leave the preceding left bend 3-9 m wide and then cross in front of the
nose; 30 of 45 nose hits were the start pack in the first 45 s.

**Own minimum-curvature line** (`racing_line.py`, FISTA in the wall corridor): walls 956 ->
1607, slower bots - REVERTED. PU's AI cuts every bend by 2-9 m (pursuit of a point 30-40 m
ahead), so a line with the apex at the wall margin goes into the inner wall (inside contacts
Gt1 12 -> 44), and the speed is planned from the SimD centre line, so a flatter line gains no
speed. NFS4's AI drove its spline like rails; its line keeps apexes further from walls.

**Learned line corrections** (`learn_line.py`): every wall contact of a batch moves the
forward line (`<track>0.lsp`) away from the side the car went to, over slices s-6..s+10,
gain 0.3 x deviation, <= 2 m per iteration, never closer than 2 m to a wall / 5 m to an
obstacle. 807 of 956 wall contacts were in 100 recurring spots, so this targets them directly.
Seeds 100+200, 38 races per row:

| line | walls | car contacts | bot stall s | bot time |
|---|---|---|---|---|
| V1 (smoothed NFS4 + obstacle clearance) | 1890 | 1071 | 553 | base |
| + 1 learning iteration | 1753 | 956 | 600 | -0.5% |
| + 2 iterations | **1543** | 957 | 552 | -0.5% |
| + 3 iterations | 1612 | 853 | 1320 | +0.2% |

The player car on aidrive keeps getting pinned for minutes (no respawn counter); its stall
seconds swing 364..2222 between variants and are left out of the judgement.

**Check on an unseen seed (300)**, V1 -> V1L2: walls 905 -> 774 (-14.5%), stalls 1155 -> 523 s
(bots 296 -> 247), unfinished 1 -> 0, car contacts 497 -> 528, bot time -0.8%. Over the three
seeds (57 races per line): walls 2795 -> 2317 (-17%), car contacts 1568 -> 1485, bot time -0.6%.
**Installed: V1L2** (`<work>/backups/lsp-V1L2`). Scripts: `tools/race_test/lab/`.

Next candidates: another learning iteration with gain 0.15 on all three seeds' contacts (the
third 0.3 iteration overshot: bot stalls 552 -> 1320); a road-edge corridor for Summit-type banks;
the spin cause (full lock + hard braking); the aidrive player's missing recovery.

A fourth, gentler iteration on top of V1L2 (gain 0.15, contacts of all three seeds; backup
`lsp-V1L3S`): walls 2317 -> 2211 but car contacts 1485 -> 1964, unfinished cars 3 -> 13, bot
stalls 800 -> 4355 s. Rejected; V1L2 stays installed. The wall count alone is not a safe target:
pushing the line away from one wall can lead the cars into a dead end or into each other.

## AI4b (2026-10-14, evening): speed zones and the line learned in slow mode

**Speed zones.** PU's speed planner FUN_0040b650 multiplies the bend severity it measures
by a per-track zone factor: FUN_004a2dc0 fills a 10-zone table at race init (int pairs
start/end at 005E74AC.., floats at 005E74FC.., > 1 = slower; EA used 0.7..1.2, a few 25/30)
for the stock tracks by name; the converted tracks get none. The wrapper now replaces the
`fmulp` + `fld [005B25FC]` (cap 115) at **0040BA8F** with a call that also multiplies by a
per-slice factor of the car's slice (car+8), `[ai] speed_zones=1`, from
`GameData/Track/<track>.aispeed` (text, `start end factor` per line, `#` comments, loaded
when the track name at 00657444 changes). Sanity check, UK with `0 5000 2.0`: walls 246 -> 51,
spins 51 -> 1, bot lap time 122 -> 179 s.

**Line learned in slow mode.** With every track at factor 2.0 the cars are slow enough that a
contact left is the line's fault, not the speed's. `learn_line.py --kinds wall,hit3 --gain 0.5
--max-step 3` on one seed, one lap, all 19 tracks per iteration (the races are deterministic,
one run shows every repeatable contact; ~3 min per iteration):

| slow mode | walls | objects | spins | bot stall s |
|---|---|---|---|---|
| V1L2 | 211 | 18 | 7 | 44 |
| it1 | 57 | 21 | 4 | 18 |
| it3 | 44 | 20 | 5 | 8 |
| **it4 (FAST4)** | **30** | 14 | 7 | 9 |
| it5 | 31 | 18 | 6 | 28 |

At full speed (no zones, seed 100, 1 lap) FAST4 vs V1L2: walls 596 -> 511, objects 161 -> 112,
spins 183 -> 173, stalls 808 -> 283 s, bot time 9848 -> 9698 s (-1.5%). **Installed: FAST4**
(`<work>/backups/lsp-FAST4`). Scripts: `lab/fast_line_loop.sh`, `lab/zone_stats_all.py`.

**Zones per track** (`race_test/learn_zones.py`, `lab/zone_all_loop.sh`): every 10-slice spot
with a wall/object contact, stall or spin gets its factor x1.15 over slices s-12..s+4 per
iteration (cap 3.0), all 19 tracks per batch.

Full speed, FAST4 line, seed 100, one lap on all 19 tracks (76 cars):

| zones | walls | objects | spins | bot stall s | car contacts | bot time |
|---|---|---|---|---|---|---|
| none | 511 | 112 | 173 | 190 | 372 | 9698 s |
| it0 (x1.15 at every spot) | 383 | 99 | 196 | 89 | 340 | +0.4% |
| it2 | 286 | 62 | 210 | 38 | 379 | +1.1% |
| it3 | 295 | 60 | 214 | 26 | 391 | +2.4% |
| it4 (**+ ramps**) | 117 | 8 | 73 | 26 | 282 | +5.6% |
| it6 | **98** | **7** | **36** | 54 | 437 | +7.4% |

Stepped zones stalled at ~290 walls with spins rising: 256 of the 355 hits of it3 were inside
zones with factor >= 1.5. Traces showed why (Aquatica 845, Atlantic 218): the car stays on full
throttle up to the zone, the factor jumps 1.0 -> 1.75, the AI brakes 220-250 at full lock inside
the bend (often on surface 3), slides 25 deg and spins when the steering reverses. A ramp in front
of every zone (factor falls by <= 0.025 per slice, `--ramp-slope`) moves the braking onto the
straight: walls 295 -> 117, objects 60 -> 8, spins 214 -> 73 in one iteration. The remaining hits
move around between iterations (pack dynamics), car contacts rise as the slower cars bunch.
Zones of it6: `<work>/backups/aispeed-Z6`.

**Speed caps (AI4c).** The severity factor cannot slow a car on the straight before a bend the
planner does not see yet: zero severity times any factor stays zero (Summit 655-670 on full
throttle at 155 km/h under factors 1.3-1.6, then braking at full lock inside the bend). The
planner's target speed is car+EB8 in m/s (at throttle-to-brake switches kmh / EB8 = 3.60);
0040BAEA stores it (`fstp [esi+EB8]`, before the surface factor <= 1 multiplies it). The wrapper
clamps it there to the cap of the car's slice: `max start end kmh` lines in the same `.aispeed`
(`[ai] speed_cap_unit`, default 1/3.6). race_trace S rows got a `target` column (EB8).
`race_test/learn_caps.py`: for the first event of an episode (no wall/object/car contact of that
car in the 3 s before) entered at >= 60 km/h, cap = entry speed (max of the 2 s before) x 0.9 over
s-4..s+2, lower caps stay; in front of it a braking curve v^2 = v_cap^2 + 2 * 4 m/s^2 * d.
`lab/cap_loop.sh`, one batch per iteration, seeds 100/200 alternating, on top of Z6:

| | walls | objects | spins | bot stall s | car contacts | bot time |
|---|---|---|---|---|---|---|
| no zones (seed 100) | 511 | 112 | 173 | 283 | 372 | 9698 s |
| Z6 factors (seed 100) | 98 | 7 | 36 | 54 | 437 | +7.4% |
| + caps it0 s100 | 78 | 7 | 18 | 18 | 393 | +7.9% |
| + caps it3 s200 | 61 | 9 | 14 | 12 | 326 | +8.5% |
| **+ caps it4 s100 (C4)** | **52** | 8 | **10** | 18 | 322 | +8.7% |

Per track <= 8 wall contacts per lap (4 cars), none on Gt1, Park, Snowy. Installed: C4
(`<work>/backups/aispeed-C4`; factors only: `aispeed-Z6`). About half of what is left are
secondary hits (after a car contact or a spin) that a cap cannot prevent. Most of the lap time
went to the Z6 factors (+7.4%); the caps cost +1.3% more. Next: give back factor slowdown where a
cap now protects the spot, to win the lap time back.

**Lap time back (AI4d).** Halving the factor excess everywhere without trouble (`learn_zones.py
--step 0 --decay 0.5`, two steps) was too much at once: seed 100 walls 52 -> 96, objects 8 -> 50,
spins 10 -> 67 for -3.4% bot time; caps at -10% per batch cannot catch up. Instead
`race_test/revert_zones.py` keeps the relaxed set (factors x0.25 excess, caps learned on it) and
restores the safe C4 factors in s-40..s+5 around every trouble spot of the two relaxed batches
(one per seed). Result T1 (`<work>/backups/aispeed-T1`, installed):

| | walls s100 | walls s200 | spins s100/s200 | bot time vs no zones |
|---|---|---|---|---|
| C4 | 52 | (61 one step earlier) | 10 / 14 | +8.7% |
| **T1** | 63 | 65 | 17 / 17 | **+6.4%** (-2.1% vs C4) |

## AI4e (2026-10-14, night): other cars - 911 GT1, 550 Spyder, 993

**Test harness caveat.** `car[N].filename` is only the 3D model. Physics come from
`car[N].specname` (Simulation/CarData/<x>.sim), AI tuning from `car[N].aispecname`
(AICarData/<x>.ais); fe.txt says `default` for both, and default.sim is the 996 Carrera 3.4
(5 dwords differ). Everything above was tuned on that. race_test got `--spec/--aispec`
(GT1 = `--car gt1 --spec GT1race --aispec Gt1`, 550 = `--car 550 --spec 550Arace --aispec 550a`).

Per 100 km, one lap of the 19 tracks (T1 zones) vs the 14 stock PU tracks:

| | converted, walls / spins | stock PU, walls / spins |
|---|---|---|
| 996 (default) | 7.8 / 2.1 | 73.5 / 22.2 |
| 911 GT1 | 8.8 / 1.0 | - |
| 550 Spyder (before) | 76.4 / 23.8 | 20.9 / 6.5 |

**550 cause 1: its AI speed table.** 550a.ais +0x80 (target m/s by bend severity index) is up to
26 % faster than default.ais in tight bends (index 22: 99 vs 79 km/h); 993.ais up to 20 %, Gt1.ais
6-10 %. The zones were learned with default. Wrapper `[ai] ref_ai_spec=default`: in the cap hook
(0040BAEA; the index is still in [ebp+8]) a car whose table is more than `ref_trigger` (1.12)
faster than the reference anywhere in indices 15-39 gets target x ref/own where own > ref, on
converted tracks only. Clamping GT1 too cost it (walls 71 -> 87-89), hence the per-car trigger.

**550 cause 2: throttle while sliding.** The AI kept 150-250 throttle at full lock while already
25-65 deg sideways; the target speed clamp alone (target <= current speed) did not lift it - the
throttle controller is slow. Hook 0040C8DA (end of FUN_0040bfe0 after the steering): EA's own rule
(halve the throttle at |steer| > 120 when [car+DB8] == 1 - with a signed-byte bug, 0040C8F4 movsx,
that turns 200 into 228; `fix_full_lock_lift` fixes it, but no tested car has DB8 == 1) and then,
on converted tracks, sliding > `slip_limit_deg` (12) -> throttle <= `slip_throttle` (64).

Tried and rejected: steering lookahead x1.3 (550 394 -> 210 walls on its 6 worst tracks, but the
996 63 -> 308: the lines are learned for x1.0), traction control keys (no effect).

Final, one lap of the 19 tracks (seed 100):

| car | walls | objects | spins | bot time |
|---|---|---|---|---|
| 550 Spyder | 622 -> **232** | 60 -> 29 | 194 -> **69** | 10925 -> 10892 s |
| 993 (real physics, clamped) | - | 61 / 16 | 4 | 10509 s |
| 996 (default) | 63 -> 51 | 10 -> 11 | 17 -> 19 | 10316 -> 10324 s |
| 911 GT1 | 71 -> 85 | 10 -> 12 | 8 -> 7 | 9577 -> 9574 s |

550 per 100 km now ~28 walls (stock tracks 21). User INI [ai]: ref_ai_spec=default,
slip_limit_deg=12, slip_throttle=64 (backup dx11z.ini.before-AI4e).

## AI4f (2026-10-14, night): reverse races

`REVERSE=1` (fe.txt key 0x23 -> 00657430): the slice number falls while driving. Wrapper: reverse
races load `GameData/Track/<track>_r.aispeed` (reloaded when track or direction changes);
race_test's lap counter also counts 0 -> top wraps; `learn_line.py / learn_zones.py /
learn_caps.py / revert_zones.py --reverse` work on `<track>1.lsp` and `_r.aispeed` with the
windows, ramps and braking curves flipped. Pipeline `lab/rev_pipeline.sh` (base / line / zones /
caps / check), one batch of 19 games per step:

| reverse, seed 100, one lap | walls | objects | spins | bot stall s | bot time |
|---|---|---|---|---|---|
| V1 lines, no zones | 503 | 96 | 178 | 212 | 9675 s |
| slow mode V1 -> RSLOW4 lines (4 learning steps) | 63 -> 19 | 1 -> 2 | 4 -> 1 | | |
| RSLOW4, no zones (full speed) | 505 | 87 | 170 | 203 | 10074 s |
| zones x1.25 + ramps, 3 steps (RZ3) | 52 | 10 | 20 | 38 | 10058 s |
| + caps, 2 steps (RC1, installed) | 67 | 8 | 16 | 10 | 10053 s |
| RC1 on seed 200 | 64 | 11 | 25 | 45 | 10114 s |
| 550 Spyder, RC1 | 162 | 17 | 81 | 44 | 10731 s |

Same level as the forward direction (996: 51-65 walls per lap). With ramps from the first step
and step 0.25, three zone iterations were enough and cost no lap time. Backups:
`<work>/backups/lsp-REV-V1` (old reverse lines), `lsp-RSLOW1..4`, `aispeed-RZ1..3`, `aispeed-RC0..1`.

## AI5 (2026-10-14, night): reset of stuck cars

The game's own reset (0040F740 per-car tick): `car+540` counts ticks only while both |car+D60| and
|car+D58| < 0.1 and calls `FUN_0040f540(car, 1, 0)` past 320 (5 s). A car wedged against a wall
that keeps rocking never counts, so it stays (reverse without zones: player 793 s on Summit, 177 s on
Germany). `FUN_0040f540` returns at once while car+540 != 0; with arg 1 it respawns on the car's
slice car+8 (an occupied slice is stepped along the race direction, REVERSE aware, FUN_0040f480 /
FUN_004817d0), zero velocity.

Wrapper (race_trace.cpp, same hook 0041041E as the trace, `[ai] stuck_reset=1`): per car, progress
in slices along the driving direction (wrap and REVERSE aware); a car that has once gone > 20 km/h
and then gains < `stuck_slices` (5) within `stuck_seconds` (6) gets car+540 = 0 and the game's
respawn. Stuck again within 20 s and < 15 slices from the last reset: car+8 is moved 10, 20, 40, at
most 80 slices on before the respawn (Germany reverse 946 trapped the car on its own slice). The
player's car only with `stuck_player=1` (race_test aidrive).

2026-10-14 (PUR1): the reset only touches racers driven on this PC: +52C & 1 and none of 10/20/80
(traffic/cop/smackable), humans only with stuck_player, +DAC == 0 (not network-driven), +B50 == 0
(not finished or out), no DERBY. Before this it reset cops (33 times in one run) and would hit
traffic, remote cars and, on a network client, the local player.

Reverse without zones, Summit/Germany/Atlantic/Hills: stall 1095 -> 132 s, unfinished 1 -> 0,
walls 215 -> 138; Summit player finishes (was stuck for good), Germany player 324 -> 157 s. With the
installed zones resets are rare (1 forward, 2 reverse per 19-track lap); lap times unchanged.
