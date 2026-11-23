# Rules for agents and contributors

Read [docs/README.md](docs/README.md) first; [docs/pipeline.md](docs/pipeline.md) and
[docs/runtime.md](docs/runtime.md) are the two pages everything else hangs on. Before you make or change a track:
[docs/track-anatomy.md](docs/track-anatomy.md) (every file and what must agree between them) and
[docs/glossary.md](docs/glossary.md). To check your work in a race: [docs/tutorial.md](docs/tutorial.md) step 2.

## Never

* **Commit game data.** No `.crp .fsh .viv .bnk .bin .qfs .frd .exe` from either game, no dumps of their textures or
  sounds, no extracted strings. `data/` holds only numbers we derived (atmosphere keys, AI speeds and lines, the
  ambience tables, the bank registry). Check `git status` before every commit.
* **Edit stock game files.** Every output is a new file or an in-memory redirect done by the runtime. Do not patch a
  stock `.crp`: fix model problems at runtime. The one exception is the editor's explicit "write to game" buttons,
  which keep a `.orig` copy.
* **Regenerate `data/ambient/pu_bank_ids.json`.** Bank numbers are baked into the `.scn` files.
* **Commit local paths or personal data.** `nfs5tools.ini` and `work/` are ignored; keep them that way.

## Hard format rules (each one cost a crash or a day)

* Textures: power-of-two squares; a track's textures fit 20 MB as ARGB8888 with mips (`nfs5_fsh.fit_budget`), or the
  race crashes in `TEXTURE_converttype`.
* Directory names: at most 15 characters, a-z 0-9 _, never a stock track's (`convert_track.check_directory`).
* `nfs5ext.trk` holds only the tracks of the last converter run: convert every wanted track in one run.
* CRP: the last LOD distance is 10000; `sn` MiscData is last; no `sf`; camera clips (`CmAn`) copied from alps;
  type-3 XObj pose from keyframe 0; every material's `mt+0` surface word set.
* Sound banks: one slot, mono EA-XA, loop start at sample 0, <= 28 000 samples, <= 0x7FFC bytes, bank number < 1000
  and not a stock one; `trackext.viv` always holds all 19 tracks.
* `Sky/*.bin`: <= 400 keys per table, terminated by a negative slice; written as template + patch.
* Track ids from the free list; name ids from 0x2000, consecutive.

## How to work

* **English** in code, comments, docs, commit messages and UI text.
* **Text and binary analysis, not screenshots.** Parse files, read logs, sample the process. Screenshots are slow and
  ambiguous.
* **Short tests.** Run a full race only when the question needs a race (physics, AI, walls, sound timing). For
  loading, lists and data checks, start the game, wait for the log line that answers the question, and kill it.
* **When stuck, stop and write it down.** Three failed attempts at the same thing = write what you tried and what you
  saw into a note and stop, instead of looping.
* **Document findings** in `docs/findings/` (what, why, addresses, what failed) and update the guide pages when a
  rule changes.
* **Acceptance scripts** live next to the code (`tools/track/test_*.py`) and run without the game window. Add one
  for every new stage. Run the relevant ones before committing:
  `python tools/track/test_hs5_geometry.py`, `test_hs6_support.py`, `test_hs7_environment.py`,
  `test_crp_writer.py`, `test_atm3_atmosphere.py` ... `test_atm8_sky.py`.
* AI tuning: lap time first; light contacts are fine, only rollovers, spins, stalls and heavy hits count as
  incidents. Launch parallel games at least 3 s apart and never run two big batches at once.
* Paths: always through `tools/track/nfs5cfg.py` (`GAME`, `NFS4_ROOT`, `WORK`, `DATA`), never hard-coded.
