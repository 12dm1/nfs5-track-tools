"""Convert High Stakes tracks into Porsche Unleashed, end to end.

    python convert_track.py --list
    python convert_track.py Hometown Gt1 --out work/build/tracks
    python convert_track.py Hometown --art-only --install <game folder>
    python convert_track.py --all --art-only --install <game folder>
    python convert_track.py --all --install <game folder>
    python convert_track.py --all --walls-only --wall-inset 0.1 --install <game folder>

Stages, each covered by its own report and acceptance script:

    HS2  textures    tr0.qfs      -> <track>.fsh
    HS5  geometry    tr.frd       -> <track>.crp
    HS6  collision   vroad, spd   -> .edg .key .jnc .dtx
    HS7  environment tr.ini, sky  -> .map .env Sky/ Cops/
    HS13 ambience    nfshs.exe    -> _audio.scn + GameData/Sounds/trackext.viv
    HS8  frontend    all of it    -> nfs5ext.trk, TrackArt, name table

Nothing shipped with the game is modified. The output is a tree of new files
plus `FEData/Data/nfs5ext.trk`, which the wrapper uses in place of nfs5.trk when
`[content] extended_content=1`. With `--install` the tree is copied into the
game folder; without it, everything stays in the output directory.
"""

import argparse
import os
import shutil
import sys

import ea_refpack as ea
import nfs4_frd as frd
import nfs4_geometry as geometry
import export_hs_walls
import nfs5_ambient as ambient
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'audio'))
import pu_track_viv
import nfs5_environment as environment
import nfs5_fsh as fsh
import track_atmosphere as atmosphere
import nfs5_frontend as frontend
import nfs5_track_files as support
import nfs5cfg

GAME = nfs5cfg.GAME
HS = nfs5cfg.HS_TRACKS
TEMPLATE_CRP = os.path.join(GAME, 'GameData', 'Track', 'alps.crp')
TEMPLATE_INDUSTRIAL_FSH = os.path.join(GAME, 'GameData', 'Track', 'industrial.fsh')
EFFECT_TEXTURE_TAGS = ('lyte', 'pole', 'cone', 'plon', 'iolp', 'lint', 'lwin', 'indl')
TEMPLATE_DTX = os.path.join(GAME, 'GameData', 'Track', 'alps.dtx')
STOCK_TRK = os.path.join(GAME, 'FEData', 'Data', 'nfs5.trk')
TRACK_DIRECTORIES = {
    # NFS4 Coastal is Dolphin Cove. Keep it separate from PU's stock coastal.
    'coastal': 'dolphin',
}


def track_directory(name):
    return TRACK_DIRECTORIES.get(name.lower(), name.lower())


def track_display_name(name):
    return frontend.track_display_name(name)


def convert_walls_only(name, out_root, inset=None):
    """Rewrite only GameData/Track/<track>.hsw (wall tuning, seconds)."""
    directory = track_directory(name)
    dest = os.path.join(out_root, 'GameData', 'Track', '%s.hsw' % directory)
    # HZ7: object walls are trimmed by texture alpha; use the installed
    # textures when this output has none.
    fsh = os.path.join(out_root, 'GameData', 'Track', '%s.fsh' % directory)
    if not os.path.isfile(fsh):
        fsh = os.path.join(GAME, 'GameData', 'Track', '%s.fsh' % directory)
    export_hs_walls.convert_hsw(os.path.join(HS, name, 'tr.frd'), dest, inset, fsh)
    return dest


def convert_crp_only(name, out_root):
    """Rewrite only GameData/Track/<track>.crp, exactly as convert_one does it
    (atmosphere light tint included)."""
    src = os.path.join(HS, name)
    directory = track_directory(name)
    dest = os.path.join(out_root, 'GameData', 'Track', '%s.crp' % directory)
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    track = frd.read(os.path.join(src, 'tr.frd'))
    atmo, _source = atmosphere.atmosphere_for(directory, src, track.n_vroad)
    tinted = any(tuple(k.light_tint) != (1.0, 1.0, 1.0) for k in atmo.keys)
    geometry.convert(
        src, dest, TEMPLATE_CRP, name=directory,
        light_tint=(lambda s: atmosphere.tint_at(atmo, s)) if tinted else None)
    return dest


def convert_sky_only(name, out_root):
    """Rewrite only GameData/Track/Sky/<Track>.fsh and .bin, exactly as
    convert_one does it."""
    src = os.path.join(HS, name)
    directory = track_directory(name)
    sky_dir = os.path.join(out_root, 'GameData', 'Track', 'Sky')
    os.makedirs(sky_dir, exist_ok=True)
    track = frd.read(os.path.join(src, 'tr.frd'))
    atmo, _source = atmosphere.atmosphere_for(directory, src, track.n_vroad)
    sky = ea.load_maybe_qfs(os.path.join(src, 'sky.qfs'))
    _h, entries = ea.fsh_dir(sky)
    ini = environment.read_ini(os.path.join(src, 'tr.ini'))
    blobs = {'fsh': environment.build_sky_fsh(ini, {e['tag']: e for e in entries},
                                              lambda e: fsh.decode_entry(sky, e)),
             'bin': atmosphere.sky_bin_for(atmo)}
    paths = []
    for ext, blob in blobs.items():
        path = os.path.join(sky_dir, '%s.%s' % (directory.capitalize(), ext))
        with open(path, 'wb') as f:
            f.write(blob)
        paths.append(path)
    return paths


def convert_one(name, out_root, verbose=True, ambient_builder=None,
                wall_inset=None):
    src = os.path.join(HS, name)
    directory = track_directory(name)
    track_out = os.path.join(out_root, 'GameData', 'Track')
    os.makedirs(track_out, exist_ok=True)

    track = frd.read(os.path.join(src, 'tr.frd'))
    stats = {'name': track_display_name(name), 'directory': directory,
             'circuit': support.is_circuit(track),
             'slices': track.n_vroad, 'blocks': track.n_blocks}

    has_lights = any(track.lights(block) for block in range(track.n_blocks))
    extra_textures = ()
    if has_lights:
        extra_textures = tuple(
            fsh.texture_from_fsh(TEMPLATE_INDUSTRIAL_FSH, tag)
            for tag in EFFECT_TEXTURE_TAGS)
    fsh.convert_qfs(os.path.join(src, 'tr0.qfs'),
                    os.path.join(track_out, '%s.fsh' % directory),
                    extra_textures=extra_textures)
    # Atmosphere first: its light_tint is baked into the CRP's df colours.
    atmo, atmo_source = atmosphere.atmosphere_for(directory, src, track.n_vroad)
    stats['atmosphere'] = dict(source=atmo_source, keys=len(atmo.keys))
    tinted = any(tuple(k.light_tint) != (1.0, 1.0, 1.0) for k in atmo.keys)
    stats.update(geometry.convert(
        src, os.path.join(track_out, '%s.crp' % directory), TEMPLATE_CRP,
        name=directory,
        light_tint=(lambda s: atmosphere.tint_at(atmo, s)) if tinted else None))
    # Keep the original NFS4 POLYVROADDATA wall edges beside the converted
    # track.  The wrapper consumes this sidecar for all converted tracks.
    stats['wall_segments'] = export_hs_walls.convert_hsw(
        os.path.join(src, 'tr.frd'),
        os.path.join(track_out, '%s.hsw' % directory), wall_inset)
    support.convert(track, directory, track_out,
                    open(os.path.join(src, 'spdfa.bin'), 'rb').read(),
                    open(os.path.join(src, 'spdra.bin'), 'rb').read(),
                    open(TEMPLATE_DTX, 'rb').read())

    sky = ea.load_maybe_qfs(os.path.join(src, 'sky.qfs'))
    _h, entries = ea.fsh_dir(sky)
    env_result = environment.convert(
        track, directory, src, track_out, {e['tag']: e for e in entries},
        lambda e: fsh.decode_entry(sky, e), atmosphere.sky_bin_for(atmo),
        nfs4_name=name, ambient=ambient_builder)
    stats['audio'] = env_result['audio']
    stats['audio_banks'] = env_result['audio_banks']

    write_track_art(name, track, out_root)

    if verbose:
        print('%-10s %s  slices %4d  articles %4d  materials %3d  '
              'primitives %6d  lamps %d/%d  sounds %d/%d banks'
              % (name, 'circuit' if stats['circuit'] else 'p-to-p ', stats['slices'],
                 stats['articles'], stats['materials'], stats['prims'],
                 stats['light_effects'], stats['light_sources'],
                 stats['audio'].get('elements', 0), len(stats['audio_banks'])))
    return stats


def write_track_art(name, track, out_root):
    """Three TrackArt files. Safe to call without rebuilding CRP."""
    art_dir = os.path.join(out_root, 'FEData', 'TrackArt')
    os.makedirs(art_dir, exist_ok=True)
    directory = track_directory(name)
    files = frontend.build_track_art(directory, track, frontend.track_title(name))
    written = []
    for filename, blob in files.items():
        path = os.path.join(art_dir, filename)
        with open(path, 'wb') as f:
            f.write(blob)
        written.append(path)
    return written


def convert_art_only(name, out_root, verbose=True):
    src = os.path.join(HS, name)
    track = frd.read(os.path.join(src, 'tr.frd'))
    written = write_track_art(name, track, out_root)
    stats = {'name': track_display_name(name),
             'directory': track_directory(name),
             'circuit': support.is_circuit(track),
             'slices': track.n_vroad, 'art': written}
    if verbose:
        print('%-10s art  %d files' % (name, len(written)))
    return stats


def make_ambient_builder():
    """Shared across tracks so bank numbers are handed out only once."""
    viv = os.path.join(GAME, 'GameData', 'Sounds', 'track.viv')
    registry = ambient.BankRegistry(ambient.free_bank_ids(viv))
    return ambient.AmbientBuilder(nfs5cfg.NFS4_ROOT, registry=registry), registry


def write_sound_archive(all_stats, out_root):
    """GameData/Sounds/trackext.viv with the banks of ALL 19 tracks.

    Built by nfs5_ambient.build_all so that converting a few tracks never drops
    the sounds of the others: the wrapper swaps the whole of track.viv for
    this file.  The `_audio.scn` of the tracks converted in this run were
    already written by the environment stage and are left as they are.
    """
    _stats, banks = ambient.build_all(GAME, [], out_root, track_directory, verbose=False)
    return banks


def register(all_stats, out_root):
    data_dir = os.path.join(out_root, 'FEData', 'Data')
    os.makedirs(data_dir, exist_ok=True)
    blob, added = frontend.build_ext_trk(STOCK_TRK, all_stats)
    with open(os.path.join(data_dir, 'nfs5ext.trk'), 'wb') as f:
        f.write(blob)
    frontend.write_name_table(os.path.join(data_dir, 'nfs5ext.names'), added)
    return added


def install(out_root, game_root):
    copied = 0
    for base, _dirs, files in os.walk(out_root):
        rel = os.path.relpath(base, out_root)
        target = os.path.join(game_root, rel) if rel != '.' else game_root
        os.makedirs(target, exist_ok=True)
        for filename in files:
            shutil.copy2(os.path.join(base, filename), os.path.join(target, filename))
            copied += 1
    return copied


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('tracks', nargs='*')
    ap.add_argument('--all', action='store_true')
    ap.add_argument('--list', action='store_true')
    ap.add_argument('--out', default=os.path.join(nfs5cfg.WORK, 'build', 'tracks'))
    ap.add_argument('--install', metavar='GAME_DIR')
    ap.add_argument('--art-only', action='store_true',
                    help='rebuild FEData/TrackArt only; do not touch CRP or nfs5ext.trk')
    ap.add_argument('--wall-inset', type=float, default=None, metavar='M',
                    help='pull the .hsw collision walls towards the road by M '
                         'metres (default export_hs_walls.WALL_INSET_M = %.2f; '
                         'negative pushes them out)' % export_hs_walls.WALL_INSET_M)
    ap.add_argument('--sky-only', action='store_true',
                    help='rewrite only GameData/Track/Sky/<Track>.fsh and .bin')
    ap.add_argument('--crp-only', action='store_true',
                    help='rewrite only GameData/Track/<track>.crp')
    ap.add_argument('--walls-only', action='store_true',
                    help='rewrite only GameData/Track/<track>.hsw')
    args = ap.parse_args()

    available = sorted(d for d in os.listdir(HS)
                       if os.path.exists(os.path.join(HS, d, 'tr.frd')))
    if args.list:
        print('tracks available: %d' % len(available))
        for n in available:
            print('   ' + n)
        return 0

    names = available if args.all else args.tracks
    if not names:
        ap.error('name the tracks or pass --all')

    out_root = os.path.normpath(args.out)
    os.makedirs(out_root, exist_ok=True)

    if args.crp_only:
        paths = [convert_crp_only(n, out_root) for n in names]
        if args.install:
            dest = os.path.join(args.install, 'GameData', 'Track')
            for path in paths:
                shutil.copy2(path, os.path.join(dest, os.path.basename(path)))
            print('crp installed: %d files -> %s' % (len(paths), dest))
        else:
            print('\n'.join(paths))
        return 0

    if args.sky_only:
        paths = [p for n in names for p in convert_sky_only(n, out_root)]
        if args.install:
            dest = os.path.join(args.install, 'GameData', 'Track', 'Sky')
            os.makedirs(dest, exist_ok=True)
            for path in paths:
                shutil.copy2(path, os.path.join(dest, os.path.basename(path)))
            print('sky installed: %d files -> %s' % (len(paths), dest))
        else:
            print('\n'.join(paths))
        return 0

    if args.walls_only:
        paths = [convert_walls_only(n, out_root, args.wall_inset) for n in names]
        if args.install:
            dest = os.path.join(args.install, 'GameData', 'Track')
            os.makedirs(dest, exist_ok=True)
            for path in paths:
                shutil.copy2(path, os.path.join(dest, os.path.basename(path)))
            print('\nwalls installed: %d files -> %s' % (len(paths), dest))
        return 0

    if args.art_only:
        all_stats = [convert_art_only(n, out_root) for n in names]
        art_files = []
        for s in all_stats:
            art_files.extend(s['art'])
        if args.install:
            dest = os.path.join(args.install, 'FEData', 'TrackArt')
            os.makedirs(dest, exist_ok=True)
            for path in art_files:
                shutil.copy2(path, os.path.join(dest, os.path.basename(path)))
            print('\nart installed: %d files -> %s' % (len(art_files), dest))
        else:
            print('\nart ready in %s (pass --install to copy into the game)'
                  % out_root)
        return 0

    builder, registry = make_ambient_builder()
    all_stats = [convert_one(n, out_root, ambient_builder=builder,
                             wall_inset=args.wall_inset) for n in names]
    registry.save()
    banks = write_sound_archive(all_stats, out_root)
    added = register(all_stats, out_root)

    print('\nambient sound: %d banks in GameData/Sounds/trackext.viv' % banks)
    print('\nregistered in nfs5ext.trk:')
    for name, track_id, name_id in added:
        print('   %-10s track id %2d, name id %d' % (name, track_id, name_id))

    if args.install:
        copied = install(out_root, args.install)
        print('\ncopied into the game: %d files -> %s' % (copied, args.install))
        print('to enable: dx11z.ini section [content], extended_content=1')
    else:
        print('\ndone in %s (without --install nothing was copied into the game)' % out_root)
    return 0


if __name__ == '__main__':
    sys.exit(main())
