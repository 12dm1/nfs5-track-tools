# `GameData/Track/<track>.tan` (animated textures, new file)

Written by `tools/track/nfs5_texanim.py`, a separate step after `convert_track.py --install` (it reads the
installed `<track>.fsh`: `python tools/track/nfs5_texanim.py --all --install <game>`). Research:
[../findings/HZ4-animated-textures.md](../findings/HZ4-animated-textures.md). The stock game ignores it.

Little-endian:

```
char   magic[4] = 'TAN1'
uint32 count
count x {
    char   tag[4]       // FSH tag of the base texture = DTEX name the game uploads it under
    uint8  kind         // 1 = frame animation, 2 = V scroll
    uint8  frames       // frame count including the base texture (kind 1)
    uint16 delay        // ticks of 1/64 s per frame
    float  scroll       // V offset per tick (kind 2): NFS4 uses 15/1024
    uint16 w, h         // frame size
    (frames - 1) * w * h * 4 bytes  BGRA pixels of frames 1..n-1 (frame 0 is the base texture)
}
```

Runtime behaviour: when a texture with a listed tag is uploaded, create the extra frames with the same processing
as game textures; at bind time pick frame `(clock64 / delay) % frames`; for scrolls add `(clock64 & 0x3FF) * scroll`
to V. The frames are taken from the **installed** `<track>.fsh`, so rebuild the `.tan` after replacing textures.

The runtime must keep the texture-name callback alive across races (HZ7): the game reinstalls its handler on every
race.
