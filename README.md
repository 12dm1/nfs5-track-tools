# nfs5-track-tools

Tools and documentation for bringing **Need for Speed: High Stakes** (NFS4, 1999) tracks into **Need for Speed:
Porsche Unleashed** (NFS5, 2000), and for editing Porsche Unleashed tracks.

All 19 High Stakes tracks convert and race: geometry, textures, walls, animated and destructible objects, sky and
fog, weather, ambient sound with object sounds, frontend art and names, and tuned AI for forward and reverse races.

**No game data is included.** You need your own copies of both games. The tools read them and write new files; no
stock game file is modified.

## What is here

| | |
|---|---|
| `tools/track/convert_track.py` | the converter: one command per track, or `--all` |
| `tools/track/` | readers and writers for the formats involved (karT `.crp`, FSH, `.edg`, `.key`, `.jnc`, `Sky/*.bin`, `.hsw`, `.tan`...), NFS4 `tr.frd` reader, acceptance tests |
| `tools/audio/` | EA `BNKl` banks (EA-XA codec), BIGF `.viv`, the NFS4 ambience extractor |
| `tools/track_editor/` | 3D editor: atmosphere along the road (fog, sky, sun, horizon, rain/snow/storm, light tint) and AI line diagnostics |
| `tools/texture_editor/` | seam and tone fixer for AI-upscaled textures |
| `tools/race_test/` | automated races and the AI tuning loops |
| `data/` | per-track atmosphere, extracted NFS4 ambience tables, tuned AI lines and speeds |
| `docs/` | setup, pipeline, the runtime contract, every format, and the research notes behind them |

## Quick start

```
python -m pip install -r requirements.txt
copy nfs5tools.example.ini nfs5tools.ini        # set [paths] game and nfs4
python tools/track/nfs5cfg.py                   # check the paths
python tools/track/convert_track.py Hometown --install "D:/Games/Porsche Unleashed"
python tools/track/nfs5_texanim.py Hometown --install "D:/Games/Porsche Unleashed"
python tools/track/install_ai_data.py --install "D:/Games/Porsche Unleashed" hometown
```

## You also need a runtime

The stock `PORSCHE.EXE` does not know about the new files. A runtime has to redirect the track list, the name
strings and the sound archive to them, and add High Stakes walls, animated textures and AI tuning.
[docs/runtime.md](docs/runtime.md) lists exactly what, with addresses. Implementations:

* **[nfs5-web](https://github.com/12dm1/nfs5-web)**: the game running in a browser; plays all 19 converted tracks.
  Reference code: `runtime/pu_ext.c`, `runtime/ext/hs_walls.cpp`, `runtime/ext/hs_ai.cpp`.
* the PC DX11 wrapper used during the research (not public; its INI keys appear in the notes).

## Documentation

Start with [docs/README.md](docs/README.md); a first session step by step: [docs/tutorial.md](docs/tutorial.md);
what a track is made of: [docs/track-anatomy.md](docs/track-anatomy.md). Tracks made by the NFS4 community (T3ED)
convert too: [docs/new-tracks-frd.md](docs/new-tracks-frd.md). For agents and contributors: [AGENTS.md](AGENTS.md).

## Credits

* NFS4 `tr.frd` semantics cross-checked against [e-rk/speedtools](https://github.com/e-rk/speedtools)
  (`specs/frd.ksy`, `frd_data.py`).
* Need for Speed, High Stakes and Porsche Unleashed are trademarks of Electronic Arts. This project is not affiliated
  with or endorsed by EA.

## License

GPL-3.0, see [LICENSE](LICENSE).
