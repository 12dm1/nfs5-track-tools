# Setup

## What you need

| | why |
|---|---|
| **Need for Speed: Porsche Unleashed**, PC, English v3.5 | the target. Its stock files are read as templates (`alps.dtx`, `monaco1` track record, `Coastal.bin`, `track.viv`, `animdefs.txt`...) and converted tracks are installed into it |
| **Need for Speed: High Stakes**, PC | the source: `Data/Tracks/<Track>/tr.frd`, `tr0.qfs`, `tr.ini`, `spdfa/spdra`, sky files; `Data/Audio/Sfx/tram*.bnk`; `nfshs.exe` (ambience tables) |
| a runtime that implements [runtime.md](runtime.md) | to play the result: the PC DX11 wrapper (`[content] extended_content=1`) or nfs5-web |
| Python 3.10+ (developed on 3.12-3.14) | all tools |
| an OpenGL 3.3 GPU | the editors only |

No game data is in this repository and none is ever written into it: tools read your copies and write into
`work/` or, with `--install`, into your game folder.

## Install

```
git clone https://github.com/12dm1/nfs5-track-tools
cd nfs5-track-tools
python -m pip install -r requirements.txt
copy nfs5tools.example.ini nfs5tools.ini     (cp on Linux/macOS)
```

Edit `nfs5tools.ini`:

```ini
[paths]
game = D:/Games/Porsche Unleashed
nfs4 = D:/Games/Need for Speed High Stakes
```

Environment variables `NFS5_GAME`, `NFS4_ROOT`, `NFS5_WORK` (and `NFS5_TOOLS_INI` for another ini file) win over the
file. Check:

```
python tools/track/nfs5cfg.py
REPO       ok      ...\nfs5-track-tools
GAME       ok      D:\Games\Porsche Unleashed
NFS4_ROOT  ok      D:\Games\Need for Speed High Stakes
HS_TRACKS  ok      ...\Data\Tracks
...
```

Every `MISSING` line is a path a tool will need. `HS_EXE` is only needed to re-extract the ambience tables
(they are already in `data/ambient/nfs4_ambient.json`).

## Conventions

* Run tools from the repository root: `python tools/track/<tool>.py ...`, or open the folder in your terminal and use
  the `run_*.bat` / `run_*.sh` launchers for the editors.
* On Windows set `PYTHONIOENCODING=utf-8` if a console prints non-ASCII paths (the launchers do).
* `--install <game>` copies into the game folder. **Close the game first**: it keeps `trackext.viv` open.
* Without `--install` output goes to `work/build/...`. `work/` is ignored by git; delete it at any time.
* Tools that modify an installed game keep backups under `work/backups/` or next to the file (`.orig`, `.bak`,
  `.pre_ai`).

## Repository layout

```
tools/track/          converter, format readers/writers, acceptance tests        -> pipeline.md
tools/audio/          EA sound banks, BIGF archives, NFS4 ambience extractor   -> pipeline/ambient-sound.md
tools/track_editor/   atmosphere + AI line editor (imgui/moderngl)             -> editor.md
tools/texture_editor/ seam/tone editor for regenerated textures                -> texture-editor.md
tools/race_test/      automated races and AI tuning loops (PC wrapper)         -> findings/AUTO1-race-test.md
data/atmosphere/      per-track atmosphere INIs (+ presets of the 20 PU tracks)
data/ambient/         extracted NFS4 ambience tables, bank number registry, by-ear overrides
data/ai/              tuned AI speed zones/profiles and racing lines for the 19 tracks
docs/                 everything else                                          -> README.md
```
