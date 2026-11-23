# Tutorial: a track from a High Stakes folder to a race, and one change back

A first session, end to end: convert Hometown into a folder of its own, race it in the browser with the AI driving,
change its fog, rebuild only the sky, race again. Nothing in either game folder is touched. Every command and every
quoted line below was run on 2026-11-23.

You need [setup.md](setup.md) done (Python, `nfs5tools.ini` with `game` and `nfs4`). To race you need a runtime:
the browser build [nfs5-web](https://github.com/12dm1/nfs5-web) (public; build it with its README: `gen.sh`,
`web/build.sh`) or the PC wrapper (not public). Words: [glossary.md](glossary.md).

## 1. Convert into a build folder

```
python tools/track/convert_track.py Hometown --out work/build/tracks
python tools/track/nfs5_texanim.py Hometown --install work/build/tracks
python tools/track/install_ai_data.py --install work/build/tracks hometown
```

About a minute and a half. `--out` without `--install` writes only into `work/build/tracks`; the two later steps
"install" into that same folder (they read the built `.fsh` and add the `.tan`, `.aispeed` and `.aiprofile`).
What to look for:

```
HS5: skipped unknown light type(s) in hometown block 62: 0xFFFF0001      <- harmless, lights PU has no type for
Hometown walls=2058 overpass=0 finite=0 objects=405 inset=0.00 -> ...hometown.hsw
Hometown   circuit  slices  959  articles  662  materials 274  primitives   5727  lamps 0/22  sounds 85/12 banks
registered in nfs5ext.trk:
   Hometown   track id 15, name id 8192
```

`work/build/tracks` now has the tree described in [track-anatomy.md](track-anatomy.md): `FEData/Data/nfs5ext.trk`,
`FEData/TrackArt/hometown*.fsh`, `GameData/Track/hometown.*`, `Sky/Hometown.*`, `racer/work/hometown0/1.lsp`,
`GameData/Sounds/trackext.viv`.

**One rule now:** `nfs5ext.trk` lists only the tracks of the last converter run. To have several tracks in the menu,
convert them in one run (`convert_track.py Hometown Gt1 Uk --out ...` or `--all`).

## 2. Race it in the browser

In the nfs5-web checkout, lay the build folder over the game as its mod layer and start the server:

```
python tools/make_mod_layer.py --pc <nfs5-track-tools>/work/build/tracks --out out/mod
run_web.bat --data "<Porsche Unleashed folder>"
```

`make_mod_layer.py` prints `missing GameData/Sounds/zzzwzzz_mod.viv` (a PC-mod file, not needed) and
`1 tracks, 28 files, 24 MB -> out/mod`. Open the page: Hometown is in the Quick Race track list.

To skip the menus, open a race directly (test parameters work only on a development server, which `run_web.bat` is):

```
http://127.0.0.1:8077/?log=1&env=NFS5_ARGS=fe%3D0%20nointromovies%3D1%20track_name%3Dhometown%20linear_track%3D0%20car%5B0%5D.aidrive%3D1
```

That is the game's command line `fe=0 nointromovies=1 track_name=hometown linear_track=0 car[0].aidrive=1`: no
frontend, this track, a circuit (`linear_track=0`; `1` for a point-to-point track), and the AI drives your car.
`log=1` sends the game's log to the server console. What it showed in 60 seconds:

```
[hs] track 'hometown' walls '.\gamedata\track\hometown.hsw': 2463      <- the track loaded, with its walls
[thrash] frame 300: 195284 draws, 259 textures, 263 uploads, 640x480   <- it draws
[hs] wall hit 1: car ... impact ...                                    <- 6 in 60 s: the AI touching walls
```

Ignore `*** GUEST MESSAGEBOX *** title: Error: terminate execution?`: a known assert of `fe=0` races (a HUD font
without a name), which the runtime answers "No".

## 3. Change the fog, rebuild only the sky

The atmosphere of a converted track lives in `data/atmosphere/<track>.ini` (the editor writes it:
[editor.md](editor.md); format: [formats/atmosphere-ini.md](formats/atmosphere-ini.md)). By hand, in
`data/atmosphere/hometown.ini`:

```ini
[key:0]
fog_colour = 200,60,60          ; was 134,134,140
```

```
python tools/track/convert_track.py Hometown --sky-only --out work/build/tracks
```

Under a second. It rewrites `Sky/Hometown.fsh` and `Sky/Hometown.bin`; in the `.bin` exactly the three colour bytes
of that key change (offset `0x1A6C`, inside the fog table that starts at `0x1A5C`). Run `make_mod_layer.py` again
(it hard-links, so it is instant) and reload the page.

Other partial rebuilds work the same way ([pipeline.md](pipeline.md) §1): `--crp-only` (geometry, and
`light_tint` from the atmosphere), `--walls-only --wall-inset 0.2` (walls 0.2 m closer to the road), `--art-only`
(menu art). Textures in HD: [texture-editor.md](texture-editor.md). AI: [pipeline/ai.md](pipeline/ai.md).

## 4. Into a PC install

With the PC wrapper, add `--install "<Porsche Unleashed folder>"` to the commands of step 1 (it copies the tree in;
stock files are never overwritten, every file is new) and set `[content] extended_content=1` in `dx11z.ini`. The
wrapper's log says `HS8 extended content: roster -> nfs5ext.trk, ...`.

## 5. When something goes wrong

* The converter stops with a Python error: the message names the stage (HS2 textures, HS5 geometry, HS6 support,
  HS7 environment, HS13 ambience); the research note of that stage explains its input. A track made by the
  community: [new-tracks-frd.md](new-tracks-frd.md).
* The track is not in the menu: `nfs5ext.trk` came from a run without it (rule in step 1), or the runtime does not
  redirect the track list ([runtime.md](runtime.md) §1).
* Everything else: [troubleshooting.md](troubleshooting.md).
