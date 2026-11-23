"""Where the games and the outputs are, for every tool in this repository.

Set them once in `nfs5tools.ini` at the repository root (copy `nfs5tools.example.ini`) or in the environment; the
environment wins.

    [paths]
    game        = D:/Games/Porsche Unleashed          NFS5_GAME     Porsche Unleashed v3.5 folder: the stock files are
                                                                     read (templates, sound archive, sky bins), converted
                                                                     tracks are installed into it with --install
    nfs4        = D:/Games/Need for Speed High Stakes  NFS4_ROOT     High Stakes folder: Data/Tracks/<Track>/tr.frd,
                                                                     Data/Audio/Sfx/tram*.bnk, nfshs.exe
    work        = ./work                               NFS5_WORK     scratch output: builds, reports, texture dumps,
                                                                     backups (never committed)

Default for nfs4: <game>/NeedforSpeedHighStakes (the layout of the development machine, where the High Stakes data sat
inside the Porsche Unleashed folder).
"""

import configparser
import os

REPO = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..'))
DATA = os.path.join(REPO, 'data')
TOOLS = os.path.join(REPO, 'tools')


def _ini():
    cp = configparser.ConfigParser()
    path = os.environ.get('NFS5_TOOLS_INI', os.path.join(REPO, 'nfs5tools.ini'))
    if os.path.isfile(path):
        cp.read(path, encoding='utf-8')
    return cp


def _get(env, key, default):
    value = os.environ.get(env)
    if not value:
        cp = _ini()
        value = cp.get('paths', key, fallback='') if cp.has_section('paths') else ''
    if not value:
        value = default
    if value and not os.path.isabs(value):
        value = os.path.join(REPO, value)
    return os.path.normpath(value) if value else value


GAME = _get('NFS5_GAME', 'game', '')
NFS4_ROOT = _get('NFS4_ROOT', 'nfs4', os.path.join(GAME, 'NeedforSpeedHighStakes') if GAME else '')
HS_TRACKS = os.path.join(NFS4_ROOT, 'Data', 'Tracks') if NFS4_ROOT else ''
HS_SFX = os.path.join(NFS4_ROOT, 'Data', 'Audio', 'Sfx') if NFS4_ROOT else ''
HS_EXE = os.path.join(NFS4_ROOT, 'nfshs.exe') if NFS4_ROOT else ''
WORK = _get('NFS5_WORK', 'work', os.path.join(REPO, 'work'))


def work(*parts):
    """A path under the scratch folder; its parent folder is created."""
    path = os.path.join(WORK, *parts)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    return path


def require_game():
    if not GAME or not os.path.isdir(os.path.join(GAME, 'GameData')):
        raise SystemExit('Porsche Unleashed folder not set or not found (%r): set [paths] game in nfs5tools.ini '
                         'or NFS5_GAME' % GAME)
    return GAME


def require_nfs4():
    if not HS_TRACKS or not os.path.isdir(HS_TRACKS):
        raise SystemExit('High Stakes Data/Tracks not found (%r): set [paths] nfs4 in nfs5tools.ini or NFS4_ROOT'
                         % HS_TRACKS)
    return HS_TRACKS


if __name__ == '__main__':
    import sys
    NAMES = ('REPO', 'TOOLS', 'DATA', 'GAME', 'NFS4_ROOT', 'HS_TRACKS', 'HS_SFX', 'HS_EXE', 'WORK')
    if '--get' in sys.argv:
        # for batch files: for /f "delims=" %%G in ('python tools/track/nfs5cfg.py --get GAME') do set GAME=%%G
        print(globals()[sys.argv[sys.argv.index('--get') + 1].upper()])
    elif '--sh' in sys.argv:
        # for shell scripts: eval "$(python tools/track/nfs5cfg.py --sh)"
        for name in NAMES:
            print("%s='%s'" % (name, globals()[name].replace('\\', '/')))
    else:
        for name in NAMES:
            value = globals()[name]
            ok = 'ok' if value and os.path.exists(value) else 'MISSING'
            print('%-10s %-7s %s' % (name, ok, value))
