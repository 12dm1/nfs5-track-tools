# AI files of converted tracks

The stock AI drives converted tracks from the racing lines and the speed planner, but it has no per-track tuning
for them. These text files add it. The runtime loads them (PC wrapper `[ai]` section, nfs5-web
`runtime/ext/hs_ai.cpp`); the stock game ignores them. Research: [../findings/AI1-converted-track-ai.md](../findings/AI1-converted-track-ai.md),
[../findings/AI6-ai-line-view.md](../findings/AI6-ai-line-view.md). Tuned files for all 19 tracks are in `data/ai/`;
install them with `python tools/track/install_ai_data.py --install <game>`.

## `GameData/Track/racer/work/<track><dir>.lsp` (stock format)

Racing line: `uint32 1`, then dense `(x, z)` float32 pairs in world space. `<dir>` 0 = forward, 1 = reverse. The
converter writes a first line from the NFS4 data (HS6); `data/ai/lines/` holds the tuned lines (smoothed, kept
away from walls and obstacles, learned from race traces).

## `GameData/Track/<track>.aispeed`, `<track>_r.aispeed` (reverse races)

Text, `#` comments, two kinds of lines:

```
start end factor        # multiply the planner's bend severity on slices start..end (> 1 = slower)
max start end kmh       # cap the AI target speed on slices start..end
```

Hooks: the severity factor replaces `fmulp` + `fld [005B25FC]` at **0040BA8F**; the cap clamps the target speed
`car+EB8` (m/s) stored at **0040BAEA**. Loaded when the track name at 00657444 or the direction (00657430) changes.

## `GameData/Track/<track>[_r][.<tag>].aiprofile`

Text, `#` comments, `slice kmh` per line: an absolute target speed per slice for the default physics
(`default.sim`/`default.ais`). When a profile exists, a racer's target speed = `profile[slice] x car scale`, and
the `.aispeed` of that track is not loaded. Header comments record how it was made
(`ai_speed.py make` parameters and `# fix a b factor` hand corrections, which survive regeneration).

## `GameData/Track/aiprofile_cars.txt`

```
<.ais name> <scale>      # e.g. "944 0.85"; "* x" = cars without a line; 0 = keep planner + .aispeed
```

The runtime identifies a racer's `.ais` by comparing its AI record (car+764) with `AICarData/*.ais` and scales the
profile by this factor. Calibrated with `tools/track_editor/ai_cars.py calibrate`.
