# Documentation

## Read in this order

1. [setup.md](setup.md): games, Python, `nfs5tools.ini`, repository layout.
2. [tutorial.md](tutorial.md): a first session: convert Hometown, race it in the browser with the AI driving,
   change its fog, rebuild only the sky.
3. [track-anatomy.md](track-anatomy.md): every file a PU track is made of, who reads it, what must agree between
   them, the smallest track that loads. Read this before changing a track by hand or writing a generator.
4. [pipeline.md](pipeline.md): converting a High Stakes track, stage by stage, the output files, the hard rules.
5. [runtime.md](runtime.md): what the game runtime must patch or hook so the converted tracks load and play. Read
   this if you are writing or porting a runtime.
6. [troubleshooting.md](troubleshooting.md): symptoms, causes, fixes, open items.

[glossary.md](glossary.md): the terms (slice, vroad, chunk, article, SimD...), coordinates, units and track names.

## Making a track of your own

* [new-tracks-frd.md](new-tracks-frd.md): today's path: a track made or edited in T3ED (the NFS4 community's
  editor), including the hundreds already published, converted like a High Stakes track. Tested on one.
* [plans/D2-own-tracks.md](plans/D2-own-tracks.md): the plan for tracks modelled in Blender (not built yet).

## Guides

* [editor.md](editor.md): the track editor (atmosphere and AI line modes).
* [texture-editor.md](texture-editor.md): seam and tone editor for regenerated textures.
* [pipeline/ambient-sound.md](pipeline/ambient-sound.md): ambient sound, PU sound limits, diagnostics.
* [pipeline/ai.md](pipeline/ai.md): AI on converted tracks, using and redoing the tuning.

## Formats

[formats.md](formats.md) indexes every file. Format pages: [sky-bin](formats/sky-bin.md),
[atmosphere-ini](formats/atmosphere-ini.md), [hsw](formats/hsw.md), [tan](formats/tan.md),
[ai-files](formats/ai-files.md).

## Research notes (`findings/`)

The reverse-engineering behind each stage, in the order it was done. They record what was tried, what failed and
the addresses involved; later notes sometimes correct earlier ones (each says so).

| note | topic |
|---|---|
| [T007](findings/T007-pu-track-formats.md) | PU track file formats (trk, map, edg, jnc, key, env, fsh, crp, dtx, scn, cop, sky) |
| [HS1](findings/HS1-nfs4-track-migration.md) | the NFS4 -> PU migration plan; NFS4 formats; the gap list (E1...) |
| [HS2](findings/HS2-texture-pipeline.md) | textures: `tr0.qfs` -> FSH |
| [HS3](findings/HS3-crp-writer.md) | the karT writer |
| [HS5](findings/HS5-geometry-conversion.md) | geometry conversion |
| [HS6](findings/HS6-collision-and-ai.md) | collision, AI route, topology |
| [HS7](findings/HS7-environment.md) | sky, reflections, tints, sound sources, police (+ HZ1 horizon strip) |
| [HS8](findings/HS8-registration.md) | registering tracks without editing game files |
| [HS9](findings/HS9-in-game-bringup.md) | first launch: respawn loop, surfaces, LOD, the render tree, cameras; engine map |
| [HS11](findings/HS11-walls-alpha.md) | walls (`.hsw`) and alpha; HS16-HS19b wall collision |
| [HS12](findings/HS12-movable-objects.md) | movable / animated / destructible objects (+ HZ2 global objects) |
| [HS13](findings/HS13-ambient-sound.md) | NFS4 and PU ambient sound |
| [HS15](findings/HS15-sound-codec-buffer-overrun.md) | the in-race crash: sound codec overrun |
| [HZ4](findings/HZ4-animated-textures.md) | animated textures, cave sky; HZ5 wall heights and shortcuts; HZ6 masks; HZ7 object collision, one-sided walls |
| [ATM1](findings/ATM1-sky-atmosphere.md) | `Sky/*.bin`, `Sky/*.txt`, sun, fog, precipitation, storms |
| [SKY1](findings/SKY1-horizon-slit.md) | the horizon slit |
| [SKY3](findings/SKY3-horizon-import.md) | importing HD horizon panoramas |
| [AI1](findings/AI1-converted-track-ai.md) | AI on converted tracks: lookahead, zones, caps, guard, stuck reset |
| [AI6](findings/AI6-ai-line-view.md) | AI line view, recorded lines, speed profiles, per-car scales |
| [AUTO1](findings/AUTO1-race-test.md) | automated races (`race_test.py`) |

Missing numbers (HS4, HS10, HS14, HS16-HS19, HZ1-HZ3...) are folded into the notes above or were tests and
acceptance runs without a separate write-up.

## Conventions in these documents

* Addresses are virtual addresses in the English `PORSCHE.EXE` v3.5 (`FUN_xxxxxxxx` = function at that address, as
  Ghidra names it; `DAT_` = global data) or in `nfshs.exe` where stated.
* `car+XXX` = offset into the PU car object.
* "The runtime" = whatever implements [runtime.md](runtime.md): the PC DX11 wrapper (not public) or nfs5-web.
  Wrapper INI keys (`[content] ...`, `[ai] ...`) are named because the research used them; nfs5-web has the same
  behaviour built in.
* `<work>` = the scratch folder (`work/` by default); `<game>` = your Porsche Unleashed folder.
