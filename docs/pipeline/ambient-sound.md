# Ambient sound on tracks converted from NFS4: guide

State (2026-09-21, object sounds 2026-10-18): sound is converted for **all 19** High Stakes tracks. The acceptance
`tools/track/test_hs14_ambient.py` passes 19/19 (295 banks, 1900 elements). Gt1 and Hometown were checked by ear;
the in-race crash is fixed.

This is the entry point for anyone who gets a sound problem. The detailed write-ups behind it:
[HS13](../findings/HS13-ambient-sound.md) (how sound works in NFS4 and in PU) and
[HS15](../findings/HS15-sound-codec-buffer-overrun.md) (the crash: why it happened and how it was fixed).

---

## 1. The whole picture

```
nfshs.exe --nfs4_ambient_scripts.py--> data/ambient/nfs4_ambient.json
                                              |  (loop zones, one-shot scripts,
tr.frd (SoundSource, vroad) ------------------|   tram bank numbers)
Data/Audio/Sfx/tramNN.bnk (the samples) ------|
data/ambient/ambient_overrides.json ----------|  (corrections made by ear)
                                              v
                     tools/track/nfs5_ambient.py  (AmbientBuilder)
                                              |
            +---------------------------------+-----------------------+
            v                                                         v
GameData/Track/<track>_audio.scn                 GameData/Sounds/trackext.viv
(AUDIO_ELEMENT: where, which bank, radius,       (593 stock banks + ours,
 volume, interval)                                bank number = 3 digits in the name)
                                                              |
                        runtime: push "%strack.viv" @004B021E -> "%strackext.viv"
```

Game files are not changed: `track.viv` stays stock. The runtime swaps the archive name only when extended content
is enabled (PC wrapper `[content] extended_content=1`) and `trackext.viv` exists. See [../runtime.md](../runtime.md).

## 2. How to build

| what | command (from the repo root) |
|---|---|
| sound for all tracks, into the game | `python tools/track/build_ambient.py --all --install <game folder>` |
| sound for some tracks | `python tools/track/build_ambient.py Hills Park --install <game folder>` |
| output only (into `work/build/ambient`) | `python tools/track/build_ambient.py --all` |
| a whole track (geometry + everything) | `python tools/track/convert_track.py Hometown --install <game folder>` |
| acceptance of what is installed | `python tools/track/test_hs14_ambient.py` |
| re-extract the tables from the exe | `python tools/audio/nfs4_ambient_scripts.py data/ambient/nfs4_ambient.json` |

The game keeps `trackext.viv` open: close it (`taskkill /F /IM PORSCHE.EXE`) before installing.

**The archive is always built from the banks of all 19 tracks**, even when `_audio.scn` is written for one: the
runtime replaces `track.viv` as a whole, and a track whose banks are missing from the archive silently loses its
sound. `convert_track.py` does the same.

## 3. Tools

| file | what it does |
|---|---|
| `tools/audio/ea_bnk.py` | `BNKl` read/write for both games; EA-XA decoder and encoder; `loop_from_start`, `fit_sample` (all PU limits); CLI `list` / `wav` / `export` |
| `tools/audio/pu_track_viv.py` | BIGF (`.viv`) read/write, finding free bank numbers |
| `tools/audio/nfs4_ambient_scripts.py` | extracts the four tables (section 4) from `nfshs.exe`; the scripts are parsed by a small register interpreter |
| `tools/track/nfs5_ambient.py` | `AmbientBuilder`: NFS4 zones -> PU elements, banks, number registry; `build_all` |
| `tools/track/build_ambient.py` | CLI for sound without rebuilding the tracks |
| `tools/track/test_hs14_ambient.py` | acceptance against the installed game |
| `data/ambient/nfs4_ambient.json` | the extracted NFS4 tables |
| `data/ambient/pu_bank_ids.json` | registry `track/slot/variant -> PU bank number`; **do not delete**: the numbers are baked into the `.scn` files |
| `data/ambient/ambient_overrides.json` | corrections made by ear (section 6) |

Listen to any bank: `python tools/audio/ea_bnk.py wav <bank> <folder>`. To find our banks in an installed archive,
`pu_track_viv.py` lists entries by number, and `pu_bank_ids.json` maps the number back to `track/slot`.

## 4. Where NFS4 sound comes from

In NFS4 the track ambience is **hard-coded in `nfshs.exe`**, not stored in data. The track index is the order of the
names in the exe (Hills, Germany, Coastal, Park, France, Uk, Snowy, Gt1, Gt2, Gt3, Hometown, Redrock, Atlantic,
RockyPas, Country, LostCany, Aquatica, Summit, Empire).

| mechanism | where | what it becomes in PU |
|---|---|---|
| track bank number | bytes at `0x5AED20` -> `tram%02d.bnk` | source of all samples |
| loop zones | `0x5AE580`: `{start, fade_in_end, fade_out_start, end, slot, side, vol}` | a chain of loops along the road, 45 m step, 60 m radius |
| one-shot scripts | functions at `0x5ADCF4`: "if slice in [A,B] and rand%MOD > THR -> slot BASE+rand%N" | a chain of elements over the range, 60 m step, 70 m radius, interval from the probability |
| script events | same functions, blocks without a probability (a bell, a hit at slice N) | one element at the slice, 45 m radius, interval ~25 s |
| `SoundSource` in the FRD | 16 bytes: 16.16 position + `type & 0xFFFF` = slot | a point element |
| animated object sounds | `0x5AE910` | a sound that follows the object: tag at `Base+0x44` + `SECTION:Audio` in `animdefs.txt` (HZ3, section 11) |

All references are direct slot indices into `tram%02d.bnk`. The `n`/`w`/`nw` variants (night/rain) are not used: PU
has no night versions of tracks.

## 5. How PU plays it

`<track>_audio.scn`, one element:

```
AUDIO_ELEMENT 4
x y z
type id range fade ivl ivl_rnd cnt cnt_rnd next slot vol flag
label
```

* `type` 0 = positional (the only one we write); 2 = non-positional; 3/4 = hard left/right.
* `id` = bank number = the three digits before `.bnk` in the archive entry name (`gt116_242.bnk` -> 242).
* `range` = audible radius, `fade` = full volume radius (m).
* `ivl`, `ivl_rnd` = pause between repeats in units of ~0.133 s (8 frames at 60 Hz; `ivl=64` ~ 8.5 s + the sample
  length). 0/0 = play continuously.
* `next slot` = runtime state, write `0 -1`. `vol` 0..100.

The chain in code: `FUN_004AFFD0` indexes the archive (1000 numbers) -> `FUN_004AF5C0` preloads banks near the start
-> `FUN_004AEF30`/`FUN_004AEAC0` drive the elements every frame (16 voices) -> `FUN_004A8570` bank cache (32) ->
mixer `0x0058F880` (separate DirectSound thread).

## 6. Tuning by ear

**Global radii and steps**: constants at the top of `nfs5_ambient.py` (`LOOP_RANGE`, `LOOP_SPACING`, `POINT_RANGE`,
`ONESHOT_SPACING`, `EVENT_*`, `LOOP_VOLUME`). The tuning history is in the comments there: at 150 m sounds started
too early, at 75 m the Gt1 crowd was inaudible at speed; now 60/70 m, modelled on the Hometown water.

**One slot**: `data/ambient/ambient_overrides.json`, key `"<NFS4 track>/<tram slot>"` in lower case:

```json
"hometown/8": {"interval_s": 7, "range": 60},
"hometown/1": {"range": 55, "volume": 55}
```

`interval_s` on a loop zone drops the loop points and turns a short loop into a sound with pauses (done for the
Hometown bird that "ticked" once a second). `range`, `spacing`, `volume` are obvious. After an edit run
`build_ambient.py` for that track (the archive is rebuilt as a whole, numbers do not change).

To find a slot by its sound: the element label in the `.scn` (`HS loop s16 slice790`), or `pu_bank_ids.json` (bank
number -> `track/slot`).

## 7. PU limits (breaking one = silence or a crash)

`fit_sample` and `loop_from_start` in `ea_bnk.py` enforce them automatically, and the acceptance checks each one.
If you change the pipeline, do not break them.

| limit | what happens if broken | where in game code |
|---|---|---|
| **a loop must start at sample 0** | race crash after 40-60 s | a loop start > 0 selects codec 7 (`0x005984D0`), its 55-sample hold buffer (`0x0059C690`) overruns and overwrites the next DSP node -> the mixer jumps into garbage (`0x005987D2 call [eax+4]`). Stock banks have loop starts 0...37 |
| <= 28 000 samples (margin below 2^15) | the bank loads but is silent | measured: everything < 32 768 played, everything above was silent |
| <= 0x7FFC bytes | the bank does not load | `0x004B07A7` |
| one slot, EA-XA, mono | unpredictable | all 593 stock banks are like this |
| bank number < 1000 and not taken by a stock bank | another bank's sound or silence | index at `0x00657310`, 1000 pairs |

Long NFS4 samples (up to 17 s) are first resampled (22050 -> ... -> 5500 Hz); if they still do not fit, they are cut:
a loop's tail is crossfaded with what came before the loop start (no click), a one-shot gets a fade-out.

**Number budget:** the stock `track.viv` has 410 free numbers, 295 are used by us, **115 are left**. If more are
needed: a runtime hook on the name parsing (`atoi(name+len-7)` in `FUN_004AFFD0`) for four-digit numbers.

## 8. Diagnostics

The PC wrapper (not public) has these switches; the same probes are worth having in any runtime:

| switch | what it gives |
|---|---|
| `[content] trace_sounds=1` | every track bank request: `HS13 bank 242 -> 4` (number -> cache slot; `-1` = still loading), summaries, the sound "gates" (`sfx`, `ambient`, voice table) and an archive index probe. **Off by default**: 18 patched call sites |
| `[audio] audio_trace_free=1` | sound arena guard: per-frame check of the DSP nodes, a `R4 NODE CORRUPT` line seconds before a crash. The best sign that the codec overruns again |
| `[audio] audio_watch_node=1` | hardware watchpoint past the end of the codec instance, a `R4 NODE WRITE` line with the writer's EIP |

A crash in sound is the thread whose entry point is `0x0058F880`.

## 9. When something is wrong

| symptom | likely cause | what to look at |
|---|---|---|
| crash 40-60 s into a race | a loop with start > 0, or new code bypassed `loop_from_start` | `R4 NODE CORRUPT` in the log; `test_hs14_ambient.py` (checks loop starts) |
| a whole track is silent | its banks are not in `trackext.viv` (archive built without it) or extended content is off | `test_hs14_ambient.py`; the `HS13: track sounds -> %strackext.viv` log line |
| one sound is silent | the bank breaks a limit, or the element is off the road, or the radius is too small for the speed | `trace_sounds=1`: does the number arrive and get a cache slot; acceptance |
| sound heard too early / from too far | radius | section 6: constants or `ambient_overrides.json` |
| sound "ticks" without pauses | a short NFS4 loop | `interval_s` for that slot |
| the wrong sound plays | bank number taken by a stock bank / registry broken | the acceptance checks collisions; do not regenerate `pu_bank_ids.json` |
| a click at the loop seam | a cut long loop | `_cut` in `ea_bnk.py` (crossfade length) |
| bank numbers "moved" after an edit | `pu_bank_ids.json` deleted or regenerated | restore it from git: `.scn` and the archive must be built with the same registry |

## 10. Not done

* Animated object sounds: done in HZ3 (section 11), except the random second sound of the Park train and three
  Empire objects with slots >= 80.
* Night/rain (`tramNNn/w/nw.bnk`): PU has no night tracks.
* 3 script blocks (Atlantic, RockyPas, Aquatica) without a slot: the slot is not a constant and the interpreter does
  not see it. The tracks sound fine without them.
* The NFS4 script update rate is assumed to be 30 Hz; one-shot intervals are computed from it. If birds are too
  frequent or too rare on some track, check that first (`NFS4_SCRIPT_HZ`).
* Gt1 and Hometown were checked by ear; the other 17 only by the acceptance.

## 11. Animated object sounds (HZ3, 2026-10-18)

PU supports a sound attached to an animated article: the stock Autobahn locomotive carries the tag `TRAN` at
`Base+0x44`, and `GameData/Track/animdefs.txt` has
`SECTION:Audio { tag=TRAN; SECTION:Sound { PatchID=833; Range=300; ... } }`. `FUN_004777F0` finds the tag
(`FUN_0047EDF0`), `FUN_004AEF30` moves the sound to the object position every frame and computes Doppler.

The record fields (parser `FUN_0047D980`, copied into `CAudioElem` +0x0E...0x20) come in the same order as the
`.scn` element fields: `PatchID` = bank, `Range`, `FadeIn` = full volume radius, `Trigger` = interval, `FadeOut` =
its random part, `MinRepeat`/`RandomRepeat` = count, `Volume`. The names are misleading. This path resets the repeat
timer every frame, so only a continuous loop works.

Pipeline:

* `nfs5_ambient.object_sound_specs(track)`: the NFS4 table per `aid` (`{radius, pan, slot, a, b, c, d, mode,
  extra}`). Radius 0 = silence (Park wagons, UK cars). Slots >= 80 are not `tram` slots and are skipped (three Empire
  objects). `mode -4` with `extra != -1` is a random single sound (the second Park train sound), skipped. `mode -3`
  (Summit cable cars, a knock every `b` frames) becomes a "sample + silence" loop with period `b/64` s (not verified
  by ear).
* The tag `N4<track base36><aid base36>` is written by the CRP converter (`nfs4_geometry`); the bank and the section
  are written by `build_all`. The bank number is the key `<track>/<1000+slot>/0` in `pu_bank_ids.json`. The block in
  `animdefs.txt` between the markers `// >>> HZ3` and `// <<< HZ3` is rewritten as a whole; the rest of the file is
  stock.
* Radius = the NFS4 radius, at most 300 m (like `TRAN`); `FadeIn` = 20 % of it.

Result: 11 objects on 7 tracks (Hometown: plane, Lost Canyons: plane, Aquatica: 2 boats, Atlantica, Empire 2, Summit:
2 cable cars, Park: one wagon of each train). A race_test probe confirmed: every object has a sound element, the bank
and radius are right, the position moves.
