# Track editor

`tools/track_editor`: a 3D preview of a Porsche Unleashed track (stock or converted) with two modes:

* **Atmosphere**: fog (sky and world channels), sky colour, sun, horizon panorama, rain/snow/storm and the baked
  light tint, all as curves along the road, with a game-accurate preview.
* **AI line**: racing lines, walls, speed zones and race traces of a track (read-only diagnostics, plus the tools
  that write AI data).

```
run_track_editor.bat [track] [--atmosphere file.ini] [--ai]      Windows (asks for a track if none given)
run_ai_line.bat [track]                                           same, opened in AI mode
./run_track_editor.sh <track> [--ai]                              Linux/macOS/Git Bash
python tools/track_editor <game>/GameData/Track <track> [--atmosphere x.ini] [--ai]
```

`<track>` is the PU directory name (`alps`, `hometown`, `dolphin`...). Loading takes ~10 s. Needs OpenGL 3.3,
`imgui-bundle`, `moderngl`, `glfw`.

Self-tests without interaction: `--selftest` (offscreen render, numbers; it expects snow, so run it on `alps`), `--ui-selftest` (hidden window, every panel
for a few frames), `--ai-selftest`, `--ai-ui-selftest`.

## Controls

* `TAB` switches the camera. **Free**: WASD + right mouse drag, Space/Ctrl up/down. **Rail**: W/S along the road by
  slice, Up/Down height. Shift = fast.
* The `Mode` strip at the top switches Atmosphere / AI line.

## Atmosphere mode

The data is `data/atmosphere/<track>.ini` ([formats/atmosphere-ini.md](formats/atmosphere-ini.md)). For a converted
track the converter created it from the NFS4 `tr.ini`. A stock track opens from its preset
(`data/atmosphere/presets/<track>.ini`, a dump of the stock `Sky/<Track>.bin`); `Save ini` then writes
`data/atmosphere/<track>.ini`, which wins from then on.

| panel | what |
|---|---|
| **File** | `Save ini`, `Load ini` (revert), `Write Sky/<Track>.bin to game` (writes straight into the game, keeping a `.orig` of the first overwritten file), presets: load any stock track's atmosphere, its keys mapped by track-length fraction |
| **Sky** | dome (radius, curvature, height, angle, scale), sun (position in the dome plane, lens flare), sky colour (`follow fog` or colours A/B), horizon (band bottom/height, 16 slots with panel and mirror), water copy |
| **Keys** | the key list: add a key at the camera slice, delete, jump the camera to a key; per-key fields (fog A/B colour and density, rain, snow, storm, region, light tint) |
| **Curves along track** | one lane per curve over the whole track; drag points; the camera slice is marked |
| **Sky layers (read-only)** | the `Sky/<Track>.txt` levels and their textures |
| **Horizon import** | replace the horizon panorama with upscaled parts ([SKY3](findings/SKY3-horizon-import.md)): choose a folder, preview, write `Sky/<Track>.fsh` to the game, restore the original |
| **Sky preview** | toggles for preview layers (clouds, horizon, sun, precipitation, lightning: `Flash now`) |

What the preview shows exactly as the game does: `texture x vertex colour` of the track (LOD 0), fog A on the sky and
horizon, fog B on the world (the game's own exponential formula with the density cube), the dome grid and sky map
layers, the horizon ring and its `hzcl` colours, the sun position. What is approximate: the particle count of
rain/snow (the game's scale factor is not decoded; the 0..1 value is transferred exactly, the visual density is
not), the cloud warp, and the `multi*` blend modes.

**Getting it into the game.** For a converted track:

```
python tools/track/convert_track.py <Track> --sky-only --install <game>      fog, sky, weather
python tools/track/convert_track.py <Track> --crp-only --install <game>      also when light_tint changed
```

(`Write Sky/... to game` in the File panel does the `.bin` part directly.) `light_tint` multiplies the baked vertex
light of every article by its slice, so it only reaches the game through a CRP rebuild. For a **stock** track only
the `.bin` button applies, and it overwrites a stock file (a `.orig` is kept); there is no CRP rebuild for stock
tracks.

Research: [ATM1](findings/ATM1-sky-atmosphere.md) (format and formulas), [SKY1](findings/SKY1-horizon-slit.md)
(horizon seams), [SKY3](findings/SKY3-horizon-import.md).

## AI line mode

Shows what the AI drives on a converted track (needs the NFS4 vroad, so converted tracks only):

| panel | what |
|---|---|
| **AI** | overlays: vroad centre, lane edges, `.hsw` walls and object walls, forward/reverse racing line (`racer/work/<track>0/1.lsp`), race trajectories, events; `On top` draws through geometry; load a race_test run (`Rescan`, `Load`) and pick cars |
| **AI events** | the run's contacts (wall, object, car, spin, stall, respawn) with filters; click to jump; `Top view` |
| **AI plots** | per-slice speed, target speed, throttle/brake and the `.aispeed` zones/caps or `.aiprofile` of the picked car |

Writing AI data (command line, see [pipeline/ai.md](pipeline/ai.md)):

```
python -m tools.track_editor.ai_record make <track>       racing line from a lap you drove
python -m tools.track_editor.ai_speed make <track>        speed profile from the line's curvature
python -m tools.track_editor.ai_batch ...                 line + profile for many tracks at once
python -m tools.track_editor.ai_cars calibrate ...        per-car profile scales
```

Research: [AI6](findings/AI6-ai-line-view.md).

## Code map

| file | role |
|---|---|
| `__main__.py` | window, loop, cameras, draw order |
| `editor.py` | editing logic (no imgui, no GL; tested headless by `test_atm7_editor.py`) |
| `state.py` | atmosphere model -> per-slice evaluation for the preview |
| `render.py` | track buffers -> fogged `texture x vertex colour` |
| `sky_render.py`, `skygeo.py`, `sky_layers.py` | sky dome, layers, horizon, sun, precipitation, lightning |
| `horizon_import.py` | SKY3 import |
| `ui.py` | atmosphere panels |
| `ai_data.py`, `ai_render.py`, `ai_ui.py` | AI mode: data, overlays, panels |
| `ai_record.py`, `ai_speed.py`, `ai_tune.py`, `ai_batch.py`, `ai_cars.py` | AI data writers and tuning loops |
| `../track/nfs5_track_mesh.py` | CRP -> draw-ready buffers |
| `../track/track_atmosphere.py` | the atmosphere model and the `.bin` writer |
