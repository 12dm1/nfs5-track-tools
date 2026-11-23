# HS15: crash in a race: sound codec buffer overrun

**Date:** 2026-09-21. **Result:** fixed in the data; neither the game nor the runtime was patched. User-verified:
3+ minute race with the reflection cubemap on, no crash, 0 `R4 NODE CORRUPT` lines in the log. General sound guide:
[../pipeline/ambient-sound.md](../pipeline/ambient-sound.md).

## Which codec index is chosen and why

`0x005984D0` sets up a voice. Its eighth argument (`[esp+0x50]` after the prologue) is the **loop start**: through
the init structure it also goes into the instance field `+0x2C`, which the loop handler divides by 28 and uses as the
loop start.

```
00598529  cmp ecx, 9   -> 0      ; format 9
00598536  cmp ecx, 0xA -> 6      ; format 0xA = EA-XA
00598542  cmp ecx, 4   -> 9
0059854E  cmp ecx, 0x40 -> 0xC
00598558  cmp [esp+0x3c], 1       ; mono
00598561  cmp [esp+0x50], 0       ; loop start
00598567  inc eax                 ; > 0  -> index + 1
```

For mono EA-XA: loop start 0 -> **index 6** (0xA0-byte instance), loop start > 0 -> **index 7** (0x128 = 296-byte
instance).

## Why index 7 overruns

Index 7: the first pass `0x0059C890` decodes the samples up to the loop start in bulk, then switches to the loop
handler `0x0059C690`, which works in blocks of 28 samples and keeps "tails" in a buffer at `+0x4C` of
`(296-0x4C)/4 = 55` samples: up to 28 from the block with the loop end and up to 27 from the block with the loop
start. Exactly enough, and only for a loop that starts inside the first block.

Stock PU banks (593): loop start 0 in 167, 1...37 samples in the rest: always within the first block or two. The NFS4
loops after the transfer started at sample 346-477: a codec path the game had never executed, and the hold buffer ran
past the end of the instance into the next DSP node (`next`, callback, `kind`). The mixer then jumped into garbage
(`0x005987D2 call [eax+4]`), 40-60 s into the race.

## What changed

`ea_bnk.loop_from_start()`: the loop is cut to the loop proper `[start, end]` and its start set to 0. For a
background sound the one-time intro is not audible. All our loops use index 6, like hundreds of stock ones. The codec
size table (`0x006B1704`) was **not touched**: it is right for the data it was made for.

The acceptance `tools/track/test_hs14_ambient.py` checks "loop start = 0" for every added bank.

## Along the way

* Banks of >= ~2^15 samples loaded into the cache but stayed silent (found with `trace_sounds`); `fit_sample` keeps
  <= 28 000.
* The first explanation, "the 16-bit cursor of the streaming window overflows" (`0x004B07E7`), was wrong: there is a
  free-space check before the add.
* The reflection cubemap only made the crash come sooner (long frames -> large mixer chunks); it was not the cause.
