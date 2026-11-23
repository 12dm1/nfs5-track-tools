# HS13: ambient sound: how NFS4 does it and how to move it into PU

> Current state, PU limits and troubleshooting: [../pipeline/ambient-sound.md](../pipeline/ambient-sound.md).
> This is the original research; some assumptions (section 6, "32 KB window") were refined later (HS15).

Date: 2026-09-21. Research + tools; the game was not started. Sources: `NeedforSpeedHighStakes/nfshs.exe` (Watcom
PE, not packed; full Ghidra export on the dev machine), `Data/Audio/Sfx/tram*.bnk`, `Data/Tracks/*/tr.frd`; PU:
`PORSCHE.EXE` (`FUN_004affd0`, `FUN_004af5c0`, `FUN_004aeac0`, `FUN_004aef30`), `GameData/Sounds/track.viv`,
`GameData/Track/*_audio.scn`. Previous state: [HS7](HS7-environment.md) "Sound" and
[HS1](HS1-nfs4-track-migration.md) 11.4, which assumed NFS4 had only 0-8 `SoundSource` per track and "no mapping
table".

---

## Conclusion

1. **`SoundSource` in the FRD is the smallest part of the NFS4 soundscape.** The main ambience (birds, wind, sea,
   construction, road) is described **inside `nfshs.exe`**: tables of loop zones by slice and 19 hand-written
   one-shot script functions, one per track. All of it is extracted into `data/ambient/nfs4_ambient.json`
   (`tools/audio/nfs4_ambient_scripts.py`).
2. **All ambient sounds of a track are in one bank `tram%02d.bnk`** (plus `n`, `w`, `nw` variants for night/rain with
   the same slot numbers), and every reference (`SoundSource.type`, zone slots, script slots) is a **direct slot
   index** into that bank. The bank number of a track is the byte table at `0x5aed20` in the exe (section 2).
3. **The formats are compatible.** Both games use `BNKl` v4 with a `PT` header and EA-XA ADPCM (15 bytes per 28
   samples). Differences: NFS4 keeps ~120 loops as PCM16 (no codec tag), PU always EA-XA with tag `0x80=1`. An encoder
   was written; moving a sample = a lossless repack or PCM16 -> EA-XA (~34 dB SNR).
4. **PU finds a bank by the number in its name.** `FUN_004affd0` builds a 1000-entry table keyed by `atoi` of the three
   digits before `.bnk` in the `track.viv` entry name. 410 numbers are free (large ranges 558-750 and 840-988). So
   **new sounds are added to the archive without a single code hook**; the 1000 limit is the only reason a hook may
   be needed later. (The runtime only redirects the archive name `track.viv` -> `trackext.viv`, so the stock archive
   stays untouched.)
5. Transfer = (a) export the needed `tram` slots into single PU banks with new numbers, (b) generate
   `<track>_audio.scn` from the four NFS4 sources (section 4), turning slice zones into positional elements along the
   road. The `AUDIO_ELEMENT` fields are decoded (section 3).

---

## 1. What HS7 got wrong

`build_audio_scn` wrote every source with the neutral id 264 and type 2. But in NFS4 `type` is a slot index into
`tram<NN>.bnk`, and there are only 0-8 sources per track because they do little of the work. 9 of 19 tracks (Hills,
Germany, France, Gt1-3, Coastal partly...) have no `SoundSource` at all, while Hills has a 70-slot bank.

## 2. NFS4: four mechanisms

Track index (order of the strings in the exe, `0x19744c`): 0 Hills, 1 Germany, 2 Coastal, 3 Park, 4 France, 5 Uk,
6 Snowy, 7 Gt1, 8 Gt2, 9 Gt3, 10 Hometown, 11 Redrock, 12 Atlantic, 13 RockyPas, 14 Country, 15 LostCany,
16 Aquatica, 17 Summit, 18 Empire.

| track | bank | track | bank | track | bank |
|---|---|---|---|---|---|
| Hills | tram12 | Uk | tram14 | Atlantic | tram02 |
| Germany | tram15 | Snowy | tram11 | RockyPas | tram03 |
| Coastal | tram10 | Gt1/2/3 | tram17 | Country | tram04 |
| Park | tram13 | Hometown | tram00 | LostCany | tram05 |
| France | tram16 | Redrock | tram01 | Aquatica | tram06 |
| | | | | Summit / Empire | tram07 / tram08 |

Bank loader `FUN_0042b920` (`"%stram%02d"` + suffix `nw`/`w`/`n`, game slot 3, handle in `DAT_005cf0bc`). Ambient
volume per track at `0x5ae978` (75; Redrock/Atlantic 63, Aquatica 45 of 127).

### 2.1 Loop zones (`0x5ae580[track]` -> records of 7 ints)

```
{start, fade_in_end, fade_out_start, end, slot, side, max_vol}   // start < 0 = end of list
```

`FUN_00421d80`: if the car's slice is in [start,end], the **loop** `slot` plays; volume ramps up over
[start,fade_in_end] and down over [fade_out_start,end]; `side` -1/0/1 = hard pan left/centre/right **relative to the
driving direction** (the sign flips when driving backwards). The first matching record wins. 73 zones on 17 tracks
(Snowy and Summit have none). Examples: Coastal 11 zones (sea slots 2/9/24/48, harbour 23, slot 70), Hills two
(0: 700-750, 1: 490-620). 68 of the 73 slots exist in the day bank, the rest only in `n`/`w`.

### 2.2 Random one-shot scripts (`0x5adcf4[track]` -> function)

Called every audio frame from `FUN_0042d2e0` (EAX = car, EDX = base pan). Each script block:

```
if slice in [A,B] (sometimes several ranges, sometimes a condition on car+0x5c4, probably the speed):
    pan = base +- a random side (0x2000|0xc000 + rand&0x3fff) or fully random
    if rand % MOD > THR:                         # probability per frame, 0.2-4.5 %
        slot = BASE + rand % N                   # bank slot group (16+, 32+, 48+, 64+)
        vol  = VOLBASE + rand % VOLRANGE         # 28..128
        SNDplay(tram, slot, pan, vol, prio = rand & 0x7f)
```

The slot groups in the `tram10+` banks (16-31, 32-47, 48-63, 64-79) are the categories of these scripts (e.g. Hills:
16+rand%4 at slices 30-50, 20+rand%10 at 3-10, 32+rand%7 on five long ranges, 48+rand%9, 64+rand%6). The parsed
records are `oneshot_scripts` in the JSON. **The decoder is heuristic**: where `ebx` is reused for the pan, the
variant count `N` comes out as garbage (65535/100/200), ~10 blocks have no recognised slot; complex nested ranges
(Coastal, Country) are merged. (The pipeline handles the known cases; see the guide.)

### 2.3 `SoundSource` in FRD blocks (16 bytes: int3 16.16 + uint32 type)

`FUN_00437d00`: `type & 0xffff` = bank slot; high word `1` = "periodic" (plays every random ~64 frames on channel
0x13), `0` = continuous loop (channel 0x12). Both channels -> the `tram` bank (`FUN_0042ce90`, 0x42cf18). Volume by
distance `0x10000 / (d^2>>10)`. 46 sources on 11 tracks; dead references: Hometown 6/7, Empire 12, LostCany 20 (slots
missing from the bank: NFS3 leftovers), Coastal 0 (only in `n`/`w`).

### 2.4 Animated object sounds (`0x5ae910[track]` -> 9 x int16 per object)

`{radius, ?, sound, rangeA0, rangeA1, rangeB0, rangeB1, mode, ?}` for each XObj-anim in FRD order (Park 11, Uk 17,
Atlantic 3, Aquatica 2, Empire 5...). Ids 80-96 are not `tram` slots but classes via `FUN_0042c6f0` (99 -> slot 1),
probably the vehicle banks `truck/limo/putt/trex.bnk`. Trains, planes, ferries. Done in HZ3 (guide section 11).

### 2.5 Weather

The `tram<NN>n/w/nw.bnk` variants replace the contents of the same slots (loops 0-3 become 0.8 s of rain noise in
rain); thunder is `rain.bnk` (v2, 8 slots). PU has no night/rain tracks, so the day bank is used and slots that exist
only in `n`/`w` are skipped.

## 3. PU: `_audio.scn` and loading

Element (`CAudioElem`, parser `'%d %d %f %f %d %d %d %d %d %d %d %d'`, compact 0x28-byte copy in `FUN_004ac170`):

```
AUDIO_ELEMENT 4
x y z
type id range fade ivl ivl_rnd cnt cnt_rnd next slot vol flag
label
```

| field | offset | meaning (from `FUN_004aeac0`/`FUN_004aef30`) |
|---|---|---|
| `type` | +0x0c | 0 positional (pan and pitch from the position, `FUN_004a6fe0/f10`); 1 special single (nearest, `FUN_004af440`, labels `Pipe`); 2 non-positional; 3/4 hard left/right (`pan -+ 0x4000`); 6/7 special volume |
| `id` | +0x0e | bank number in `track.viv` (three digits before `.bnk`) |
| `range` | +0x10 | audible radius (m); within `range + DAT_005b4bb0` the bank is preloaded |
| `fade` | +0x14 | full volume radius; linear falloff between `fade` and `range` |
| `ivl`, `ivl_rnd` | +0x18/+0x1a | next start = now + `ivl` + rand % `ivl_rnd` (unit `DAT_00606ac4 >> 3`; 0/0 = play continuously, the bank's own loop decides) |
| `cnt`, `cnt_rnd` | +0x1c/+0x1e | repeat count `cnt + rand % (cnt_rnd+1)` |
| `next`, `slot` | +0x22/+0x24 | runtime state (next start time, channel -1): the stock editor saved the live state; write `0 -1` |
| `vol` | +0x20 | 0..100 |
| `flag` | +0x26 | byte; for type 1 "already loaded" |

Loading: `FUN_004affd0`: `track.viv` -> a `trck index` table of 1000 (offset,size) pairs, index =
`atoi(name + strlen - 8)`; check `id < 1000 && size > 0`. `FUN_004af5c0` ("Loading track sounds...") preloads the
banks of elements around the start; `FUN_004a8570(bank=0, id)` is a 32-bank cache. All 593 banks of `track.viv` have
one slot each, EA-XA, 22050/16000/11025 Hz.

## 4. Transfer plan (as built, see the guide for the final constants)

| NFS4 source | PU element |
|---|---|
| loop zone `[start,end]`, `slot`, `side` | a chain of `type 0` elements along the road, offset from the road axis by `side x (half width + margin)` (side 0 on the axis), `ivl=ivl_rnd=cnt=0`, volume scaled from `max_vol`; lower volume at the ends instead of fade in/out. Positional elements give the right pan in both directions, better than `type 3/4` |
| script block: ranges, `p`, `BASE+rand%N`, `vol`, side | elements spread over the range beside the road; `ivl` from the probability (at 30 Hz `p=0.0061` ~ once per 5.5 s), `ivl_rnd = ivl`, `cnt=1`; `vol` = middle of the range |
| continuous `SoundSource` | one `type 0` element at the point, `ivl=0` |
| periodic `SoundSource` | the same with `ivl ~ 64` frames |
| animated objects | HZ3 |

`tram` slots -> PU banks: `python tools/audio/ea_bnk.py export tramNN.bnk <slot> <label>_<id>.bnk`, then
`python tools/audio/pu_track_viv.py ...`. One NFS4 slot with several variants (`0xFE` sub-headers, up to 4) = several
banks. Numbering is kept in the registry `data/ambient/pu_bank_ids.json`.

## 5. Tools

* `tools/audio/ea_bnk.py`: `BNKl` read/write (both games), EA-XA decoder + encoder, PCM16; CLI `list` / `wav`
  (listen to slots) / `export`. The slot table starts at `0x14` (the dword at `0x10` is not an entry; in the first
  pass the indices were off by one because of it).
* `tools/audio/pu_track_viv.py`: BIGF writer, add/replace banks by number, list free numbers. Round trip is byte
  exact.
* `tools/audio/nfs4_ambient_scripts.py`: extracts the tables of 2.1, 2.2, 2.4, bank numbers and volumes from the
  exe -> `data/ambient/nfs4_ambient.json`.
* `tools/track/nfs5_ambient.py`: `AmbientBuilder`, the generator.

## 6. Not verified at HS13 time (see HS15 and the guide)

* Whether a PU bank needs the tag `0x80=1` or accepts NFS4's `0x83=7`: the writer writes PU style.
* The `ivl` unit (`DAT_00606ac4 >> 3`): frames or ms; from the "Crow" labels (`16 56`) about 0.27 s at 30 Hz (the
  guide uses ~0.133 s per unit, measured).
* The `car+0x5c4` field in script conditions (compared with 3...11): probably the speed; affects 8 blocks.
* How often `FUN_0042d2e0` runs (probability -> period). Assumed 30 Hz (`NFS4_SCRIPT_HZ`).
