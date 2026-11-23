# Troubleshooting

Symptoms seen while bringing the 19 tracks up, with the cause and where the full story is. If you change the
converter, check the "Hard rules" in [pipeline.md](pipeline.md) section 4 first.

## Loading and the frontend

| symptom | cause | fix / where |
|---|---|---|
| the track is not in the list | the runtime does not redirect `nfs5.trk` (extended content off), or `nfs5ext.trk` was not installed | [runtime.md](runtime.md) item 1; reinstall with `convert_track.py --install` |
| the name shows as an empty or wrong string | the `GetString` hook is missing, or `nfs5ext.names` ids are not consecutive from 0x2000 | runtime item 2 |
| crash while loading germany / gt2 / uk | a non-square or non-power-of-two texture (the caller `FUN_00535bc0` dereferences a null page) | `nfs5_fsh.ensure_square_pot`; never feed raw sizes |
| crash while loading after a hand edit of the CRP | `sn` MiscData not last (binary search on the ident) or an `sf` entry present | [HS3](findings/HS3-crp-writer.md), [HS9](findings/HS9-in-game-bringup.md) |
| loading finishes, then a beige screen, a blue screen and a hang (100 % CPU) | infinite respawn loop: no material had a drivable surface nibble, so the quadtree had no "kind 1" faces to place the car on | the `mt+0` surface word, [HS9 section 3](findings/HS9-in-game-bringup.md) |
| no start fly-by, camera starts in the bumper | no `CmAn` camera clips in the CRP | copied from `alps.crp`, HS9 7.1 |

## Drawing

| symptom | cause | fix / where |
|---|---|---|
| the track loads but no geometry is drawn | a type-3 XObj (movable object) inflated the render tree AABB | the pose comes from keyframe 0, HS9 6.5, [HS12](findings/HS12-movable-objects.md) |
| geometry pops in late or disappears at distance | the last LOD distance is 65535 instead of 10000 | `nfs5_crp_writer.FAR_DISTANCE`, HS9 section 4 |
| light fringe / a line along the quad edge on hedges and trees | binary-alpha masks drawn with blending and WRAP | renderer side, [HZ6](findings/HZ4-animated-textures.md) |
| vertical slit or a seam in the horizon panorama | border clearing of cutouts erased the atlas edge columns | [SKY1](findings/SKY1-horizon-slit.md) |
| fog the same blue on every track, snow out of nowhere | an old pipeline copied `alps.bin` / `Coastal.bin` | the `.bin` is generated from `data/atmosphere/<track>.ini`, [ATM1](findings/ATM1-sky-atmosphere.md) |
| black cave fog does not black out the sky | fog A density too low, or the runtime discards densities >= 1 | HZ4 section 3, runtime item 7 |
| water / fire static | no `.tan`, or the texture-name hook dies after the first race | `nfs5_texanim.py`, runtime items 5-6 |
| no lens flare | the runtime lacks THRASH `readrect` | runtime "nice to have" |

## Driving

| symptom | cause | fix / where |
|---|---|---|
| you drive through walls | no `.hsw`, or the runtime has no wall hook | runtime item 4, [HS11](findings/HS11-walls-alpha.md) |
| the car "stops" at a wall with no sound or sparks | the runtime clamps the position instead of calling the game's hit functions | runtime item 4 (`FUN_00494000`, `FUN_00494df0`) |
| a phantom head-on hit while sliding along a wall | the hit was predicted along the car's forward axis | swept SAT, HS19 in HS11 |
| the car catches on a joint between two wall segments | a car-axis SAT on a shared segment end | shared-end rule, HS19b in HS11 |
| fenced in on a deck or ramp you should drop off | every wall was 16 m high | the edge byte is the height in 1/4 m, [HZ5](findings/HZ4-animated-textures.md) |
| respawned when taking a shortcut | the SimD corridor ended at the vroad walls | corridor widened on shortcuts (HZ5); for an old install `apply_corridor.py --restore` |
| you can drive through posts, cacti, trees | no object walls (old `.hsw` without the `HSX1` tail) | `convert_track.py --walls-only`, HZ7 |
| "invisible ceiling" hit on the Lost Canyons shortcut | open (HZ8) | not fixed |
| camera jerks on painted road lines | `.edg` edges with flag `0x00` | HS9 7.4 |
| wrong grip / sounds on a surface | the `mt+0` surface nibble (1 asphalt, 3 grass, 5 dirt, 9 snow...) | HS9 7.6 |

## AI

| symptom | cause | fix / where |
|---|---|---|
| bots brake late and hit walls everywhere | 4.6 m slices vs PU's 6.0 m | lookahead x1.3, runtime item 8 |
| bots stuck against a wall for minutes | the stock 5 s reset counts only while the car is still | stuck reset, AI5 in [AI1](findings/AI1-converted-track-ai.md) |
| some cars much worse than others | their `.ais` speed table is faster than `default.ais` | `aiprofile_cars.txt`, reference clamp, [pipeline/ai.md](pipeline/ai.md) |
| reverse races drive like forward ones | `_r.aispeed` / `<track>1.lsp` missing | `install_ai_data.py` |

## Sound

See the table in [pipeline/ambient-sound.md](pipeline/ambient-sound.md) section 9. The two that matter most:

* **a crash 40-60 s into a race** = a sound loop that does not start at sample 0 (codec 7 overruns its 55-sample hold
  buffer and corrupts the next DSP node);
* **a whole track silent** = its banks are not in `trackext.viv` (the archive must hold all 19 tracks) or the runtime
  does not redirect `track.viv`.

## Known open items

* HZ8: an invisible ceiling on the Lost Canyons shortcut.
* The sun sprite is not fogged in the Lost Canyons cave.
* Sound: 3 NFS4 script blocks without a slot (Atlantic, RockyPas, Aquatica), the random second sound of the Park
  train, three Empire object sounds (slots >= 80); 17 of 19 tracks checked only by the acceptance, not by ear.
* Night and rain variants of NFS4 tracks are not converted (PU has no night tracks).
* Atmosphere: the `multi*` sky blend modes, cloud warp, `move ratio` and the precipitation particle scale are not
  decoded; the editor preview is approximate there.
