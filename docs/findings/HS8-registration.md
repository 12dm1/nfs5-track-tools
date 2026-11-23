# HS8: registering tracks without editing game files

Date: 2026-09-18. Stage 7 of the plan in [HS1](HS1-nfs4-track-migration.md). Code: `tools/track/nfs5_frontend.py`,
`tools/track/convert_track.py`; the runtime side was `extended_content.{h,cpp}` in the PC wrapper (see
`docs/runtime.md` for the full runtime contract).

## What was done

The whole pipeline runs with one command:

```
python tools/track/convert_track.py Hometown Gt1
Hometown   circuit  slices  959  articles  591  materials 150  primitives 5530  sounds 6
Gt1        circuit  slices  832  articles  747  materials 102  primitives 5577  sounds 0

registered in nfs5ext.trk:
   Hometown   track id 15, name id 8192
   Gt1        track id 17, name id 8193
```

The output is a tree of files: `GameData/Track/*` (geometry, textures, collision, AI, sky, cops), `FEData/TrackArt/*`
(3 files per track) and `FEData/Data/nfs5ext.trk` + `nfs5ext.names`.

## Requirement met: zero edits of original files

```ini
[content]
extended_content=0     ; default: the game runs stock
```

With 0 the install function is not called and **not one byte of the process is patched**. With 1 two in-memory
patches work:

**1. Track list.** The string `'nfs5.trk'` is at `0x005DB328`. The module scans the code section and rewrites the
4-byte operand of every `push 0x005DB328` found in `0x004FAD00...0x004FB200` (the loader `FUN_004FAE20` and the list
init `FUN_004FB750`); exactly 7 are expected, and if the count differs the patch is not applied at all. The operand
then points at the string `"nfs5ext.trk"` owned by the DLL. The stock `nfs5.trk` is not rewritten.

`nfs5ext.trk` itself is the 15 stock records copied byte for byte plus one per converted track. The loader takes the
record count from the file size (`filesize / 0x4A8`), so appending is enough.

**2. Track names.** `FEStrings.csv` is not touched. Instead **every call** of `GetString` (`0x004E06D0`), 154 in the
executable, is redirected to a hook that answers its own id range (from `0x2000`) and passes everything else to the
original. The names are read from `FEData/Data/nfs5ext.names`, written by the converter; if the ids in the table are
not consecutive the table is rejected with a message instead of being used blindly.

Patching all call sites instead of detouring the function was deliberate: a detour needs a trampoline with copied
instructions, here it is enough to rewrite the operands of instructions we checked byte by byte.

## The `nfs5.trk` record is built from a template

Of the 1192 bytes only the ids, five string fields and the type flags are understood. So a record is copied from an
existing one and patched, like the CRP materials in HS3. The template is **monaco1**, because it is a circuit and all
of ours are circuits (HS6).

Patched:

| offset | field |
|---|---|
| 0 | track id, from the free list: 15, 17, 19-24, 27, 28, 31, 33, then from 35 |
| 4 | name id: `0x2000 + n`, matches `NAME_ID_BASE` in the runtime |
| 8 | folder (`hometown`) |
| 28 | reverse direction prefix (`bk`) |
| 48 | environment name (`Hometown`) |
| 68 / 88 | splash names (`hometownSp`, `hometownSpSm`) |
| 108 | properties, bit `0x10000` = circuit |

Checked: the generated file reads back, 17 records, the new ones have `props = 0x10003`, the circuit flag is set.

Track names: the official HS names (`HS_TRACK_NAMES` in `nfs5_frontend.py`, from `Data/TEXT/TEXT.MET` of High
Stakes). NFS4 Coastal is installed as `dolphin` (Dolphin Cove) so it does not collide with PU's own `coastal`.

## Frontend art with a real track map

The structure follows the originals: `<dir>.fsh` = map + name strip + two `Fini`; `<name>Sp.fsh` = five 256x192
splashes + `star` + `fin `; `<name>SpSm.fsh` = small map 163x119 + `s` + `f`.

The track map is **not a placeholder**: the vroad centre line is projected onto the XZ plane, fitted into the frame
and drawn, the start marked red. The frontend shows the real track shape. The splashes use the same drawing (no
photos).

Risk checked at launch: the original art is ARGB4444 (`0x6D`) and 8-bit palette (`0x7B`), we write ARGB8888 (`0x7D`).
The frontend accepted it (HS9).

## How to try

```
python tools/track/convert_track.py --all --install <game folder>
```

then enable the extended content in the runtime (PC wrapper: `[content] extended_content=1`; the web build loads the
mod layer automatically). The PC wrapper log shows
`HS8 extended content: roster -> nfs5ext.trk, N name call sites hooked, M names`. To go back set
`extended_content=0`; nothing has to be deleted, no original file changed.

## What only a launch could check (answered in HS9)

* whether the engine accepts our CRP;
* whether the frontend copes with a 16th and later record or 15 is hard-coded somewhere (it copes: 34 tracks);
* whether the frontend is picky about its art pixel format (it is not);
* whether the bots drive with the calibrated speed scale (HS6);
* how the sky looks and whether the player gets stuck in a barrier gap.

## Next

HS9: batch run of all 19 tracks and what the launch revealed.
