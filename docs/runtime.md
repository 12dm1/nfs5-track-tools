# Runtime contract: what the game needs to run converted tracks

The converter only writes **new files**. No stock file is edited (not `nfs5.trk`, not `FEStrings.csv`, not
`track.viv`, not any stock `.crp`). The stock `PORSCHE.EXE` (v3.5) ignores the new files, so something has to
redirect the game to them in memory and add the behaviour that High Stakes tracks need and PU lacks. That
"something" is the runtime.

Two implementations exist:

| runtime | where | status |
|---|---|---|
| PC DX11 wrapper (`dx11z.dll`, THRASH driver) | not public | reference; INI `[content] extended_content=1`, `[ai] ...` |
| nfs5-web (the game recompiled to WebAssembly) | https://github.com/12dm1/nfs5-web: `runtime/pu_ext.c`, `runtime/ext/hs_walls.cpp`, `runtime/ext/hs_ai.cpp`, texture animation in `runtime/web/r_web.c` | public, plays all 19 tracks |

If you write a third one (a DLL hook, an exe patcher, a reimplementation), implement the items below. Items 1-4 are
required for a track to load and play; 5-8 are needed for it to play *well*.

All addresses are for the English `PORSCHE.EXE` v3.5.

## 1. Track list: `nfs5ext.trk` instead of `nfs5.trk`

The string `'nfs5.trk'` is at `0x005DB328`. Rewrite the 4-byte operand of every `push 0x005DB328` in
`0x004FAD00...0x004FB200` (the loader `FUN_004FAE20` and the list init `FUN_004FB750`) to point at your own
`"nfs5ext.trk"`. Exactly **7** are expected; if the count differs, do not patch at all.

`FEData/Data/nfs5ext.trk` = the 15 stock records byte for byte + one 0x4A8-byte record per converted track. The
loader takes the count from the file size. 34 tracks are known to work. Record layout: [findings/HS8-registration.md](findings/HS8-registration.md).

## 2. Track names: `GetString` ids from 0x2000

`GetString` is `0x004E06D0` (cdecl, one argument: the string id). Track records point at name ids `0x2000 + n`.
Answer ids in that range from `FEData/Data/nfs5ext.names` and pass everything else to the original:

```
# id	name	caps	short	short caps
8192	Aquatica	AQUATICA	Aquatica	AQUATICA
```

The game asks for `id`, `id+0x14`, `id+0x28`, `id+0x3C` = display / CAPS / short / SHORT CAPS (the four columns).
Ids must be consecutive; reject the table otherwise. The PC wrapper redirects all 154 call sites; nfs5-web overrides
the function. Either works.

## 3. Sound archive: `trackext.viv` instead of `track.viv`

At `0x004B021E` the game pushes the format `"%strack.viv"`. Replace the operand with `"%strackext.viv"` **only if**
`GameData/Sounds/trackext.viv` exists. The archive holds the 593 stock banks plus the converted tracks' banks, so
stock tracks keep their sound. Details and limits: [pipeline/ambient-sound.md](pipeline/ambient-sound.md).

## 4. Walls: `GameData/Track/<track>.hsw`

PU's own barrier (`.edg`) is a single corridor per slice; NFS4 tracks have real walls, overpasses and shortcuts.
The `.hsw` file ([formats/hsw.md](formats/hsw.md)) carries them, and the runtime collides the car with them.

* **Which tracks are converted.** The ones with a `<track>.hsw` beside the CRP (the path at `00628964` with `.crp`
  replaced by `.hsw`). Do not keep a list of names: community tracks convert under names of their own, and nfs5-web's
  name list made one race without walls and without the AI of §8 (changed to the `.hsw` test on 2026-11-23). The
  same flag turns on §8.

* **Hook point.** `00410E25` stores `00498FF0` into `car+E2C`, the per-car collision step callback. Its prologue
  `83 EC 08 53 8B 5C 24 10` is 8 bytes of whole instructions without relative addresses, so it is safe to detour.
  Run your code before the original step (pre phase) and after it (post phase).
* **Which track.** Load `<track>.hsw` by the CRP path of the loaded world, without `.crp`. No `.hsw` = do nothing
  (stock tracks).
* **Shape.** Collide the car **OBB** (half extents from the car object; `car+3A8` is the half width) against the
  wall segments with a separating-axis test in XZ. A wall applies only while the car centre is below
  `base + height - 0.2`. With the `HSX1` flag 1, test a wall only when the car centre is on its free side (left of
  a->b).
* **The hit must be the game's own.** Clamping the position alone makes the car "stop" silently. Hand the contact to
  the stock functions, the same way `FUN_00495020` does for `.edg` edges:
  * `FUN_00494000(car, normal, point, push, 1)`: impulse `vel += n * (-vn * 1.1)`, yaw kick, hit sound;
  * `FUN_00494df0(car, normal, 1)`: damage panel and sparks.

  Both are cdecl (`00495521: add esp, 18h`). `point` = the body support point along -n (Y = centre),
  `push = n * depth`.
* **Hit at the moment of contact, not a prediction.** First move the car back along -n to the contact (`sep`), then
  call the game. Use a **swept** SAT (contact time = the latest entry over the wall normal and the car axes,
  translational sweep of the step) when the car is not already inside a wall. A static "largest separation axis"
  prediction produces phantom head-on hits (HS19).
* **Shared segment ends.** Where two segments meet (ends within 5 cm) and the chosen axis is a car axis, use the
  wall normal instead, and drop a sweep contact that is not moving into that normal. Otherwise the car catches on
  the seam (HS19b).

Full history and the reasoning behind each rule: [findings/HS11-walls-alpha.md](findings/HS11-walls-alpha.md).
Reference code: nfs5-web `runtime/ext/hs_walls.cpp` and `hs_walls_math.h`.

## 5. Texture names that survive a race restart

Anything keyed by texture name (animated textures, material rules) needs the DTEX name (= FSH tag) of each upload.
The game passes it through the callback slot `006A3A50`, but on every race the texture manager reinstalls its own
handler (`push 004677D0` at `00467F68` and `00467F81` -> setter `005556A1`). Patch both `push`es to your callback,
or your hook silently disappears from the second race on (HZ7).

## 6. Animated textures: `GameData/Track/<track>.tan`

[formats/tan.md](formats/tan.md). On upload of a listed tag create frames 1..n-1; at bind time choose the frame from
a 64 Hz clock; for scrolls add the V offset at draw time. Without this, water and fire on 13 tracks are static
(not broken).

## 7. Fog density >= 1

The game cubes the interpolated fog density and writes it into the driver fog state (Glide state `0x0E`; mode
`0x15`, colour `0x0F`). Converted tracks use densities up to 0.9 (cube 0.73) to black out caves; accept any value up
to 64. A runtime that discards values >= 1 as garbage turns the fog off instead (HZ4/HZ7).

## 8. AI on converted tracks

Converted tracks have 4.6 m slices (PU: 6.0 m) and no per-track zone table in the exe, so the stock AI enters bends
too fast. What the reference runtimes do (all only on converted tracks, i.e. when an `.hsw` is loaded):

| what | where | data |
|---|---|---|
| speed planner lookahead x 6.0/4.6 | `0040B6FD` (`local_c` distance) | none |
| per-slice bend severity factor | replace `fmulp` + `fld [005B25FC]` at `0040BA8F` | `<track>[_r].aispeed` `start end factor` |
| per-slice target speed cap | `0040BAEA` (`fstp [esi+EB8]`, target m/s) | `.aispeed` `max start end kmh` |
| per-slice speed profile (replaces the two above when present) | `0040BAEA` | `<track>[_r].aiprofile` x `aiprofile_cars.txt` |
| target guard: move a blocked steering target sideways | `0040C865 call 00532330` (target - position, esi = car) | `.hsw` |
| lift the throttle while sliding > 12 deg | `0040C8DA` (end of `FUN_0040bfe0`) | none |
| reference AI table clamp for cars whose `.ais` speed table is much faster than `default.ais` | `0040BAEA` (index in `[ebp+8]`) | `AICarData/*.ais` |
| stuck reset: progress < 5 slices in 6 s -> `car+540 = 0` and the game's respawn `FUN_0040f540(car, 1, 0)` | per-car tick near `0041041E` | none |

Reload the AI files when the track name (`00657444`) or the direction (`00657430`) changes. Formats:
[formats/ai-files.md](formats/ai-files.md). Measurements and rejected variants:
[findings/AI1-converted-track-ai.md](findings/AI1-converted-track-ai.md),
[findings/AI6-ai-line-view.md](findings/AI6-ai-line-view.md). Reference code: nfs5-web `runtime/ext/hs_ai.cpp`.

Without item 8 the tracks are drivable, but bots hit walls about 2.5 times more often and respawn a lot.

## Nice to have (renderer quality, not track-specific)

* **Lens flares** need THRASH `readrect` (the game reads a 4x4 block at the sun every 200 ms to test occlusion).
* **Masked textures** (hedges, trees, fences): NFS4 masks are binary alpha with an alpha-test threshold of 10/255,
  some drawn without depth write. Treat binary-alpha textures as hard cutouts (threshold 0x80, clamp, inset) to
  avoid fringes and quad-edge lines (HZ6).
* **Tunnel lamps / additive sky map layer**: stock behaviour, listed only because DX ports often get it wrong.

## How to check a runtime

1. `python tools/track/convert_track.py Hometown --install <game>` and
   `python tools/track/install_ai_data.py --install <game> hometown`.
2. Start the game: Hometown must be in the Quick Race track list with its name (items 1-2).
3. Race it: ambient sound near the water (3), you cannot drive through the walls and hitting one gives a sound and
   sparks (4), the water texture animates (5-6).
4. `python tools/race_test/race_test.py` with the bot driving the player car (see
   [findings/AUTO1-race-test.md](findings/AUTO1-race-test.md)) for a scripted check (8). The race_test tool expects
   the PC wrapper's command line and logs; adapt it for another runtime.
