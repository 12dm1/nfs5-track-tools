"""AUTO1: automated race runs without the frontend, several games in parallel.

    python tools/race_test/race_test.py --track gt1 hometown --car 993
        [--runs 3] [--parallel 6] [--opponents 3] [--laps 2] [--seconds 300]
        [--time-scale 2] [--seed 100] [--no-aidrive] [--set key=value ...]

The game's own dev path: PORSCHE.EXE reads fe.txt (the frontend -> race
hand-off) and then parses command-line KEY=VALUE pairs with the same parser
(FUN_004b6660 -> FUN_004b60d0 -> FUN_004b5470), so arguments override fe.txt.
fe=0 skips the frontend and starts the race fe.txt describes;
car[0].aidrive=1 lets the AI drive the player's car. fe.txt is not modified.

Each game is started suspended and patched in memory (PORSCHE.EXE on disk is
untouched): 0056562B jne->jmp ignores the single-instance mutex, 004B0F02
makes a lost focus no-op (the race would pause otherwise). Every game gets its
own directory: DX11Z_OUT (dx11z.log, dx11z_trace.csv, dx11z_phys.log) and
DX11Z_INI (a copy of dx11z.ini: 640x480 window, no cube map / MSAA / vsync,
[autotest] trace=1 and time_scale). The wrapper's race trace (race_trace.cpp)
records every car each 4 simulation ticks, every impact and every respawn.

Memory read while it runs: car objects 005E8B34[8] (0 = player), slice
car+8, sim tick 00606AC4, race setup 00658080 and carInfo 0065808C stride 580.

Output: <work>/reports/race_test/<stamp>_batch/<run>/summary.json per game and
report.json / report.md for the batch: verdicts, race and lap times of every
car, impacts by kind, respawns, stops, and the track spots (slice bins) where
those events pile up across runs. Exit code 1 if any run FAILed.
"""
import argparse
import concurrent.futures as cf
import csv
import ctypes
import ctypes.wintypes as wt
import datetime
import json
import math
import os
import re
import shutil
import statistics
import struct
import subprocess
import sys
import threading
import time
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'track'))
import nfs5cfg  # noqa: E402

GAME = Path(nfs5cfg.GAME)
EXE = GAME / 'PORSCHE.EXE'
INI = GAME / 'dx11z.ini'
PLAYER_LST = GAME / 'SaveData' / 'player.lst'   # emptied by a crash in a race
REPORTS = Path(nfs5cfg.WORK) / 'reports' / 'race_test'

CAR_PTRS = 0x005E8B34        # car objects [8], index 0 = player (HS corridor, N1)
CAR_SLICE = 0x008            # int16
SIM_TICK = 0x00606AC4        # race simulation tick, 64 per second
TICKS = 64.0
NUM_CARS = 0x00658080        # +4 player race cars, +8 opponent race cars
CAR_INFO = 0x0065808C        # fe.txt car[N] block, stride 0x580
CAR_INFO_STRIDE = 0x580
INFO_AIDRIVE = 0x144
INFO_FILENAME = 0x228

# (address, original bytes, patched bytes)
PATCHES = [
    (0x0056562B, b'\x75\x4D', b'\xEB\x4D'),   # single-instance mutex: always "not running"
    (0x004B0F02, b'\x74\x1F', b'\x74\x60'),   # WM_ACTIVATE(inactive): skip the pause
]

# Test INI: only rendering cost and the autotest section change.
INI_OVERRIDES = {
    'display': {'width': '640', 'height': '480', 'mode': 'windowed', 'reflection_cubemap': '0',
                'reflection_shading': '0', 'reflection_probe': '0', 'msaa': '1',
                'alpha_to_coverage': '0', 'motion_blur': '0', 'vsync': '0', 'precise_timer': '1',
                'texture_aniso': '1'},
    'materials': {'log': '0'},
    'perf': {'enable': '0'},
}

POLL = 0.5
# The games run from a byte-identical copy of PORSCHE.EXE. Windows applies the
# AcGenral shim to PORSCHE.EXE by name (registry layer IgnoreFreeLibrary<dx11z>
# and the system compatibility database; __COMPAT_LAYER does not remove it),
# and that hook crashed on the NVIDIA driver thread (AcGenral+B5246) in
# parallel runs. The copy loads without it.
TEST_EXE = GAME / 'PORSCHE_autotest.exe'


def test_exe():
    with exe_lock:
        if not TEST_EXE.exists() or TEST_EXE.read_bytes() != EXE.read_bytes():
            shutil.copy2(EXE, TEST_EXE)
    return TEST_EXE
print_lock = threading.Lock()
lst_lock = threading.Lock()
exe_lock = threading.Lock()


def say(*parts):
    with print_lock:
        print(*parts, flush=True)


kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
ntdll = ctypes.WinDLL('ntdll')
kernel32.OpenProcess.restype = wt.HANDLE
kernel32.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
kernel32.ReadProcessMemory.restype = wt.BOOL
kernel32.ReadProcessMemory.argtypes = [wt.HANDLE, ctypes.c_void_p, ctypes.c_void_p,
                                       ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]
kernel32.WriteProcessMemory.restype = wt.BOOL
kernel32.WriteProcessMemory.argtypes = [wt.HANDLE, ctypes.c_void_p, ctypes.c_void_p,
                                        ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]
kernel32.VirtualProtectEx.restype = wt.BOOL
kernel32.VirtualProtectEx.argtypes = [wt.HANDLE, ctypes.c_void_p, ctypes.c_size_t, wt.DWORD,
                                      ctypes.POINTER(wt.DWORD)]
kernel32.FlushInstructionCache.argtypes = [wt.HANDLE, ctypes.c_void_p, ctypes.c_size_t]
kernel32.CloseHandle.argtypes = [wt.HANDLE]
ntdll.NtResumeProcess.argtypes = [wt.HANDLE]


class GameMemory:
    def __init__(self, pid):
        # VM_OPERATION | VM_READ | VM_WRITE | QUERY_LIMITED
        self.h = kernel32.OpenProcess(0x0008 | 0x0010 | 0x0020 | 0x1000, False, pid)

    def read(self, addr, size):
        if not self.h or not addr:
            return None
        buf = ctypes.create_string_buffer(size)
        got = ctypes.c_size_t()
        if not kernel32.ReadProcessMemory(self.h, addr, buf, size, ctypes.byref(got)):
            return None
        return buf.raw if got.value == size else None

    def write_code(self, addr, data):
        old = wt.DWORD()
        if not kernel32.VirtualProtectEx(self.h, addr, len(data), 0x40, ctypes.byref(old)):
            return False
        n = ctypes.c_size_t()
        ok = kernel32.WriteProcessMemory(self.h, addr, data, len(data), ctypes.byref(n))
        kernel32.VirtualProtectEx(self.h, addr, len(data), old.value, ctypes.byref(old))
        kernel32.FlushInstructionCache(self.h, addr, len(data))
        return bool(ok) and n.value == len(data)

    def u32(self, addr):
        b = self.read(addr, 4)
        return struct.unpack('<I', b)[0] if b else None

    def slices(self):
        """{car index: slice} for every live car object."""
        b = self.read(CAR_PTRS, 32)
        out = {}
        for i, car in enumerate(struct.unpack('<8I', b) if b else ()):
            d = self.read(car, 10) if car else None
            if d:
                out[i] = struct.unpack_from('<h', d, CAR_SLICE)[0]
        return out

    def setup(self):
        """The race setup the game actually uses (fe.txt + command line)."""
        b = self.read(NUM_CARS, 12)
        if not b:
            return None
        n, players, opponents = struct.unpack('<3i', b)
        cars = []
        for i in range(max(0, min(n, 8))):
            base = CAR_INFO + i * CAR_INFO_STRIDE
            name = (self.read(base + INFO_FILENAME, 32) or b'').split(b'\0')[0].decode('latin1')
            cars.append({'filename': name, 'aidrive': self.u32(base + INFO_AIDRIVE)})
        return {'numcars': n, 'players': players, 'opponents': opponents, 'cars': cars}

    def close(self):
        if self.h:
            kernel32.CloseHandle(self.h)
            self.h = None


class LogTail:
    """Follows one game's dx11z.log."""
    PAGEFLIP = re.compile(r'^pageflip frame=(\d+)')
    TRACK = re.compile(r"^SMK: ==== load \d+ track='([^']*)'|^HS corridor: track='([^']*)'")
    WARN = re.compile(r'R4 NODE CORRUPT|does not start as expected|read fault')

    def __init__(self, path):
        self.path = path
        self.pos = 0
        self.rest = b''
        self.frames = []        # (wall time, frame)
        self.track = None
        self.uef = 0
        self.veh = 0
        self.trace_on = False
        self.crash_blocks = []
        self.warnings = []
        self._crash_lines = None

    def poll(self, now):
        try:
            with open(self.path, 'rb') as f:
                f.seek(self.pos)
                data = f.read()
        except OSError:
            return
        self.pos += len(data)
        lines = (self.rest + data).split(b'\n')
        self.rest = lines.pop()
        for raw in lines:
            self._line(raw.decode('latin1').rstrip('\r'), now)

    def _line(self, line, now):
        if self._crash_lines is not None:
            if line.startswith(('  ', 'EAX', 'ESP', 'Exception', 'Access', 'track table')):
                if len(self._crash_lines) < 14:
                    self._crash_lines.append(line)
                return
            self.crash_blocks.append(self._crash_lines)
            self._crash_lines = None
        m = self.PAGEFLIP.match(line)
        if m:
            self.frames.append((now, int(m.group(1))))
            return
        if line.startswith('=== CRASH UEF'):
            self.uef += 1
            self._crash_lines = [line]
        elif line.startswith('=== CRASH VEH'):
            self.veh += 1
            if len(self.crash_blocks) < 8:
                self._crash_lines = [line]
        elif line.startswith('AUTO1 trace: per-car tick'):
            self.trace_on = True
        m = self.TRACK.match(line)
        if m:
            self.track = m.group(1) or m.group(2)
        if self.WARN.search(line) and len(self.warnings) < 40:
            self.warnings.append(line)

    def finish(self):
        if self._crash_lines is not None:
            self.crash_blocks.append(self._crash_lines)
            self._crash_lines = None

    def fps_between(self, t0, t1):
        pts = [(t, f) for t, f in self.frames if t0 <= t <= t1]
        if len(pts) < 2 or pts[-1][0] <= pts[0][0]:
            return None
        return (pts[-1][1] - pts[0][1]) / (pts[-1][0] - pts[0][0])


# ----------------------------------------------------------------- setup ---

def track_is_ring(name):
    """Ring flag (props +108 bit 0x10000) of the track record, as the frontend sees it."""
    for trk in ('nfs5ext.trk', 'nfs5.trk'):
        path = GAME / 'FEData' / 'Data' / trk
        if not path.exists():
            continue
        data = path.read_bytes()
        for off in range(0, len(data) - 0x4A7, 0x4A8):
            if data[off + 8:off + 40].split(b'\0')[0].decode('latin1').lower() == name.lower():
                return bool(struct.unpack_from('<I', data, off + 108)[0] & 0x10000)
    return None


def game_args(a, job):
    track, car = job['track'], job['car']
    args = {'fe': '0', 'nointromovies': '1'}
    if track:
        args['track_name'] = track
        ring = track_is_ring(track)
        if ring is not None:            # the frontend sets it from the track record
            args['linear_track'] = '0' if ring else '1'
    if car:
        args['car[0].filename'] = car
    if a.opponents is not None:
        args['numopponentracecars'] = str(a.opponents)
        args['numcars'] = str(1 + a.opponents)
        for i in range(1, 1 + a.opponents):
            args[f'car[{i}].class'] = 'ai'
            args[f'car[{i}].filename'] = a.opp_car or car or '993'
    # car[N].filename is only the 3D model; physics and AI tuning come from
    # specname (GameData/Simulation/CarData/<x>.sim) and aispecname
    # (AICarData/<x>.ais), "default" in fe.txt (default.sim ~ 996 Carrera 3.4).
    ncars = 1 + (a.opponents if a.opponents is not None else 0)
    for i in range(ncars):
        if a.spec:
            args[f'car[{i}].specname'] = a.spec
        if a.aispec:
            args[f'car[{i}].aispecname'] = a.aispec
    if a.laps:
        # One lap more than measured: the game ends the race (and with fe=0
        # exits) when the player finishes, before the bots get their time.
        args['num_laps'] = str(a.laps + 1)
    if job.get('seed') is not None:
        args['rand_seed'] = str(job['seed'])
    if not a.no_aidrive:
        args['car[0].aidrive'] = '1'
    for kv in a.set or []:
        k, _, v = kv.partition('=')
        args[k.strip()] = v.strip()
    return [f'{k}={v}' for k, v in args.items()]


def write_test_ini(path, a):
    overrides = {s: dict(kv) for s, kv in INI_OVERRIDES.items()}
    overrides['autotest'] = {'trace': '1', 'trace_every': str(a.trace_every),
                             'time_scale': str(a.time_scale)}
    explicit_display = {}
    for item in getattr(a, 'ini', None) or []:
        key, _, value = item.partition('=')
        section, _, name = key.partition('.')
        overrides.setdefault(section.strip().lower(), {})[name.strip().lower()] = value.strip()
        if section.strip().lower() == 'display':
            explicit_display[name.strip().lower()] = value.strip()
    if a.full_render:
        # keep the user's [display] as it is, except keys given with --ini display.KEY=...
        overrides.pop('display')
        if explicit_display:
            overrides['display'] = explicit_display
    elif not getattr(a, 'show', False):
        # the window goes to the taskbar unfocused (wrapper [display] start_minimized)
        overrides['display'].setdefault('start_minimized', '1')
    out, section, seen = [], None, defaultdict(set)

    def flush_missing(sec):
        for k, v in overrides.get(sec, {}).items():
            if k not in seen[sec]:
                out.append(f'{k}={v}')

    for line in INI.read_text(encoding='utf-8-sig', errors='replace').splitlines():
        m = re.match(r'\s*\[([^\]]+)\]', line)
        if m:
            if section is not None:
                flush_missing(section)
            section = m.group(1).strip().lower()
            out.append(line)
            continue
        m = re.match(r'\s*([A-Za-z0-9_]+)\s*=', line)
        if m and section in overrides and m.group(1).lower() in overrides[section]:
            key = m.group(1).lower()
            seen[section].add(key)
            out.append(f'{key}={overrides[section][key]}')
            continue
        out.append(line)
    if section is not None:
        flush_missing(section)
    for sec, kv in overrides.items():
        if not any(re.match(r'\s*\[' + sec + r'\]', l, re.I) for l in out):
            out.append(f'[{sec}]')
            out.extend(f'{k}={v}' for k, v in kv.items())
    path.write_text('\n'.join(out) + '\n', encoding='utf-8')


def launch(argv, out):
    """Start PORSCHE.EXE suspended, apply PATCHES, resume. Returns (Popen, GameMemory).
    RACE_TEST_DRIVER picks a test wrapper, e.g. Drivers\\dx11pur -> Drivers\\dx11purz.dll,
    so experiments do not touch the installed Drivers\\dx11z.dll."""
    env = dict(os.environ, THRASH_DRIVERNAME=os.environ.get('RACE_TEST_DRIVER', r'Drivers\dx11'),
               DX11Z_OUT=str(out), DX11Z_INI=str(out / 'dx11z.ini'))
    proc = subprocess.Popen([str(test_exe())] + argv, cwd=str(GAME), env=env,
                            creationflags=0x00000004)   # CREATE_SUSPENDED
    mem = GameMemory(proc.pid)
    for addr, orig, new in PATCHES:
        cur = mem.read(addr, len(orig))
        if cur != orig or not mem.write_code(addr, new):
            proc.kill()
            raise RuntimeError(f'patch at {addr:08X} failed (found {cur!r})')
    ntdll.NtResumeProcess(int(proc._handle))
    return proc, mem


# -------------------------------------------------------------- analysis ---

def read_trace(path):
    """dx11z_trace.csv of the first race in the file -> (S rows, H rows, R rows).
    After the finish the game may start the same race again (tick back to 0)."""
    S, H, R = [], [], []
    try:
        lines = path.read_text(encoding='latin1').splitlines()
    except OSError:
        return S, H, R
    for line in lines:
        if line.startswith('#'):
            if S:
                break
            continue
        p = line.split(',')
        try:
            if p[0] == 'S' and len(p) >= 9:
                S.append((int(p[1]), int(p[2]), int(p[3]), float(p[4]), float(p[5]), float(p[6]),
                          float(p[7]), int(p[8])))
            elif p[0] == 'H' and len(p) >= 13:
                H.append((int(p[1]), int(p[2]), int(p[3]), float(p[4]), float(p[6]), float(p[7]),
                          float(p[8]), int(p[9], 16)))
            elif p[0] == 'R' and len(p) >= 9:
                R.append((int(p[1]), int(p[2]), int(p[3]), float(p[4]), float(p[6]),
                          float(p[7]), float(p[8])))
        except ValueError:
            continue
    return S, H, R


class LapCounter:
    """Counts slice wraps on a ring. A car on the grid behind the line crosses
    it at once (slice ~top -> ~0): that is not a lap, so a wrap only counts
    after the car has been in the middle of the track since the last one."""

    def __init__(self):
        self.mid = False

    def step(self, prev, cur, top):
        if top <= 100:
            return False
        if top * 0.4 < cur < top * 0.6:
            self.mid = True
        # forward races wrap top -> 0, reverse races (REVERSE=1: the slice
        # number falls) wrap 0 -> top
        if (prev > top * 0.75 and cur < top * 0.25) or (prev < top * 0.25 and cur > top * 0.75):
            counted, self.mid = self.mid, False
            return counted
        return False


EPISODE_GAP = 32          # ticks (0.5 s) between contacts of one episode
STALL_SLICES = 3
STALL_SECONDS = 8.0


def slice_gap(a, b, top):
    d = abs(a - b)
    return min(d, top + 1 - d) if top > 100 else d


def impact_kind(cls):
    """car+438 high word: 4 = wall (.edg edge or HS corridor), 5 = another car."""
    kind = (cls >> 16) & 0xFFFF
    return {4: 'wall', 5: 'car'}.get(kind, f'hit{kind:X}')


def analyse_trace(S, H, R, laps_wanted, min_impact):
    """Per car: distance, speeds, wraps (laps) with ticks, impacts, respawns, stops."""
    by_car = defaultdict(list)
    for row in S:
        by_car[row[1]].append(row)
    top = max((r[2] for r in S), default=0)
    start_tick = None
    for rows in by_car.values():
        moving = next((r[0] for r in rows if r[6] > 1.0), None)
        if moving is not None:
            start_tick = moving if start_tick is None else min(start_tick, moving)
    cars, events = {}, []
    for car, rows in sorted(by_car.items()):
        laps = LapCounter()
        wraps = [b[0] for a, b in zip(rows, rows[1:]) if laps.step(a[2], b[2], top)]
        # The game runs laps_wanted + 1 laps; after its finish a car parks
        # behind the line: nothing after that counts (it is not a stall).
        end_tick = wraps[laps_wanted] if laps_wanted and len(wraps) > laps_wanted else None
        if end_tick is not None:
            rows = [r for r in rows if r[0] <= end_tick]
        car_H = [h for h in H if h[1] == car and (end_tick is None or h[0] <= end_tick)]
        car_R = [r for r in R if r[1] == car and (end_tick is None or r[0] <= end_tick)]
        dist = 0.0
        for p, q in zip(rows, rows[1:]):
            step = math.hypot(q[3] - p[3], q[5] - p[5])
            if step < 50:                           # a respawn teleports the car
                dist += step
        lap_times = []
        prev = start_tick
        for w in wraps:
            if prev is not None:
                lap_times.append(round((w - prev) / TICKS, 2))
            prev = w
        finish = None
        if laps_wanted and len(wraps) >= laps_wanted and start_tick is not None:
            finish = round((wraps[laps_wanted - 1] - start_tick) / TICKS, 2)
        # A stall: after the start the car stays within STALL_SLICES slices for
        # STALL_SECONDS or more (pinned on a wall, in a dead end, in a pile-up).
        # The game's own stopped counter (car+540) misses a car pushing a wall.
        stops = []
        anchor = None
        live = [r for r in rows if start_tick is not None and r[0] >= start_tick + 5 * TICKS]
        for r in live + [None]:
            if r is not None and anchor is not None and slice_gap(r[2], anchor[2], top) <= STALL_SLICES:
                last = r
                continue
            if anchor is not None and (last[0] - anchor[0]) / TICKS >= STALL_SECONDS:
                hits = sum(1 for h in car_H if anchor[0] <= h[0] <= last[0])
                stall = {'type': 'stall', 'car': car, 'tick': anchor[0], 'slice': anchor[2],
                         'x': anchor[3], 'z': anchor[5],
                         'seconds': round((last[0] - anchor[0]) / TICKS, 1), 'hits': hits}
                stops.append(stall)
                events.append(stall)
            anchor = last = r
        imp = [h for h in car_H if h[6] >= min_impact]
        # Contacts of one kind closer than EPISODE_GAP ticks are one episode:
        # a car scraping a wall for 10 s is one wall episode, not 640 hits.
        episodes = []
        open_ep = {}
        for h in sorted(imp):
            kind = impact_kind(h[7])
            ep = open_ep.get(kind)
            if ep is not None and h[0] - ep['last'] <= EPISODE_GAP:
                ep['last'] = h[0]
                ep['contacts'] += 1
                ep['impact'] = max(ep['impact'], h[6])
                continue
            ep = {'type': kind, 'car': car, 'tick': h[0], 'last': h[0], 'slice': h[2],
                  'x': h[3], 'z': h[4], 'kmh': round(h[5]), 'impact': h[6], 'contacts': 1}
            open_ep[kind] = ep
            episodes.append(ep)
        for ep in episodes:
            ep['seconds'] = round((ep.pop('last') - ep['tick']) / TICKS, 2)
            ep['impact'] = round(ep['impact'], 1)
            events.append(ep)
        kinds = Counter(ep['type'] for ep in episodes)
        resp = car_R
        for r in resp:
            events.append({'type': 'respawn', 'car': car, 'tick': r[0], 'slice': r[2],
                           'x': r[3], 'z': r[4]})
        cars[car] = {
            'distance_m': round(dist), 'max_kmh': round(max(r[6] for r in rows)),
            'avg_kmh': round(statistics.mean(r[6] for r in rows)),
            'laps': len(wraps), 'lap_times_s': lap_times, 'race_time_s': finish,
            'contacts': len(imp), 'impacts': len(episodes), 'impacts_by_kind': dict(kinds),
            'contact_seconds': round(sum(ep['seconds'] for ep in episodes), 1),
            'respawns': len(resp), 'stops': len(stops),
            'stall_seconds': round(sum(s['seconds'] for s in stops), 1),
            'moved': any(r[6] > 5 for r in rows),
        }
    return {'start_tick': start_tick, 'top_slice': top, 'cars': cars, 'events': events}


# ------------------------------------------------------------------ run ---

def stop_game(proc):
    """Kill one game; never raise (a game can take a while to leave the driver)."""
    if proc.poll() is not None:
        return
    try:
        proc.kill()
        proc.wait(15)
    except Exception:
        subprocess.run(['taskkill', '/F', '/T', '/PID', str(proc.pid)],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            proc.wait(30)
        except Exception:
            say(f'    game {proc.pid} did not exit')


def run_one(a, job, batch_dir, player_lst):
    name = f"{job['index']:03d}_{job['track'] or 'fetxt'}_{job['car'] or 'fetxt'}" + (
        f"_s{job['seed']}" if job.get('seed') is not None else '')
    out = batch_dir / name
    out.mkdir(parents=True, exist_ok=True)
    write_test_ini(out / 'dx11z.ini', a)
    argv = game_args(a, job)
    say(f'--- {name}: start')
    t_launch = time.monotonic()
    try:
        proc, mem = launch(argv, out)
    except Exception as ex:
        say(f'    {name}: FAIL launch: {ex}')
        return {'name': name, 'job': job, 'verdict': 'FAIL', 'fails': [f'launch: {ex}'],
                'warnings': [], 'notes': []}
    tail = LogTail(out / 'dx11z.log')

    setup = None
    t_race = tick_race = None
    end_reason = exit_code = None
    last_tick, last_tick_change = None, time.monotonic()
    wraps = defaultdict(int)
    last_slice = {}
    counters = {}
    top_slice = 0
    while True:
        time.sleep(POLL)
        now = time.monotonic()
        tail.poll(now)
        code = proc.poll()
        if code is not None:
            exit_code, end_reason = code, 'exited'
            break
        tick = mem.u32(SIM_TICK)
        if t_race is not None and tick is not None and last_tick and tick + 64 < last_tick:
            end_reason = 'finished'         # the game started the race again after the finish
            break
        if tick != last_tick:
            last_tick, last_tick_change = tick, now
        sl = mem.slices()
        if 0 in sl and tick:
            if t_race is None:
                t_race, tick_race = now, tick
                say(f'    {name}: race at {now - t_launch:.1f}s')
            if setup is None and now - t_race > 1.0:
                setup = mem.setup()
            top_slice = max([top_slice] + list(sl.values()))
            for i, s in sl.items():
                p = last_slice.get(i)
                if p is not None and counters.setdefault(i, LapCounter()).step(p, s, top_slice):
                    wraps[i] += 1
                last_slice[i] = s
        if t_race is None and now - t_launch > a.load_timeout:
            end_reason = 'no_race'
            break
        if t_race is not None and now - last_tick_change > a.hang_seconds:
            end_reason = 'hang'
            break
        if t_race is not None and tick and (tick - tick_race) / TICKS >= a.seconds:
            end_reason = 'time'
            break
        if a.laps and wraps and all(wraps[i] >= a.laps for i in last_slice) and \
                len(wraps) >= len(last_slice):
            end_reason = 'laps'
            time.sleep(1.0)                 # let the trace flush the finish
            break
    t_end = time.monotonic()
    stop_game(proc)
    mem.close()
    time.sleep(0.5)
    tail.poll(t_end)
    tail.finish()
    restored = False
    with lst_lock:
        if player_lst and (not PLAYER_LST.exists() or PLAYER_LST.read_bytes() != player_lst):
            PLAYER_LST.write_bytes(player_lst)
            restored = True

    S, H, R = read_trace(out / 'dx11z_trace.csv')
    tr = analyse_trace(S, H, R, a.laps, a.min_impact)
    fps = tail.fps_between(t_race, t_end) if t_race else None
    sim_rate = ((last_tick - tick_race) / (last_tick_change - t_race)
                if t_race and last_tick and last_tick_change > t_race + 1 else None)

    fails, warns, notes = [], [], []
    if end_reason == 'no_race':
        fails.append(f'no race within {a.load_timeout}s (player car never appeared)')
    if end_reason == 'hang':
        fails.append(f'simulation stopped for {a.hang_seconds}s (hang)')
    if end_reason == 'exited' and exit_code == 0 and not tail.uef and t_race:
        end_reason = 'finished'             # the race ended and fe=0 closes the game
        notes.append('the game finished the race and closed itself')
    if end_reason == 'exited':
        fails.append(f'game exited by itself (code {exit_code})'
                     + (', unhandled exception logged' if tail.uef else ''))
    if tail.uef and end_reason != 'exited':
        fails.append('unhandled exception (CRASH UEF) logged')
    if (job['track'] and tail.track and tail.track.isprintable() and '(' not in tail.track
            and tail.track.lower() != job['track'].lower()):
        fails.append(f"loaded track '{tail.track}', asked for '{job['track']}'")
    if setup:
        if job['car'] and setup['cars'] and setup['cars'][0]['filename'].lower() != job['car'].lower():
            fails.append(f"car[0] is '{setup['cars'][0]['filename']}', asked for '{job['car']}'")
        if a.opponents is not None and setup['opponents'] != a.opponents:
            fails.append(f"{setup['opponents']} opponents in the race, asked for {a.opponents}")
    if t_race and not tail.trace_on:
        warns.append('wrapper trace not installed (old dx11z.dll?)')
    if end_reason in ('time', 'laps', 'finished'):
        for i, st in tr['cars'].items():
            if i == 0 and a.no_aidrive:
                continue
            if not st['moved']:
                (fails if i == 0 else warns).append(f'car {i} never moved')
            if st['respawns']:
                warns.append(f"car {i}: {st['respawns']} respawn(s)")
    if restored:
        notes.append('SaveData/player.lst was changed by the run and has been restored')
    if tail.veh:
        notes.append(f'{tail.veh} first-chance access violation(s) handled')
    for w in tail.warnings[:5]:
        warns.append('log: ' + w[:160])
    verdict = 'FAIL' if fails else ('WARN' if warns else 'PASS')

    summary = {
        'name': name, 'job': job, 'verdict': verdict, 'fails': fails, 'warnings': warns,
        'notes': notes, 'args': argv, 'track_loaded': tail.track, 'setup': setup,
        'end': end_reason, 'exit_code': exit_code,
        'load_seconds': round(t_race - t_launch, 1) if t_race else None,
        'wall_seconds': round(t_end - t_launch, 1),
        'race_sim_seconds': round((last_tick - tick_race) / TICKS, 1) if t_race and last_tick else 0,
        'fps': round(fps, 1) if fps else None,
        'sim_ticks_per_s': round(sim_rate, 1) if sim_rate else None,
        'crash_uef': tail.uef, 'crash_veh': tail.veh,
        'trace': {k: v for k, v in tr.items() if k != 'events'},
    }
    (out / 'summary.json').write_text(json.dumps(summary, indent=2), encoding='utf-8')
    (out / 'events.json').write_text(json.dumps(tr['events']), encoding='utf-8')
    with open(out / 'log_excerpt.txt', 'w', encoding='utf-8') as f:
        for block in tail.crash_blocks:
            f.write('\n'.join(block) + '\n\n')
        f.write('\n'.join(tail.warnings) + '\n')
    if not a.keep_log and verdict != 'FAIL':
        try:
            (out / 'dx11z.log').unlink()
        except OSError:
            pass
    pc = tr['cars'].get(0, {})
    say(f"    {name}: {verdict} [{end_reason}] sim {summary['race_sim_seconds']}s "
        f"x{summary['sim_ticks_per_s'] and round(summary['sim_ticks_per_s'] / TICKS, 1)} "
        f"| player {pc.get('laps', 0)} laps {pc.get('race_time_s')}s hits {pc.get('impacts', 0)} "
        f"| {'; '.join(fails + warns[:2])}")
    summary['events'] = tr['events']
    return summary


# ---------------------------------------------------------------- report ---

def event_cost(e):
    """Rough seconds lost: a stall costs its length, a respawn ~15 s, a wall or
    object contact 1 s plus its length, a car contact half of that."""
    t = e['type']
    if t == 'stall':
        return e['seconds']
    if t == 'respawn':
        return 15.0
    if t == 'car':
        return 0.5 + 0.5 * e.get('seconds', 0.0)
    return 1.0 + e.get('seconds', 0.0)


def batch_report(results, batch_dir, a):
    by_track = defaultdict(list)
    for r in results:
        by_track[r['job']['track']].append(r)
    report = {'args': {k: (str(v) if isinstance(v, Path) else v) for k, v in vars(a).items()},
              'tracks': {}}
    md = [f'# race_test batch {batch_dir.name}', '',
          f'{len(results)} runs, laps {a.laps}, opponents {a.opponents}, time scale {a.time_scale}', '']
    for track, runs in by_track.items():
        verdicts = Counter(r['verdict'] for r in runs)
        player_times, bot_times = [], []
        hits_per_car, resp, stops = [], 0, 0
        kinds = Counter()
        for r in runs:
            for car, st in r.get('summary_cars', r.get('trace', {}).get('cars', {})).items():
                car = int(car)
                if st.get('race_time_s') is not None:
                    (player_times if car == 0 else bot_times).append(st['race_time_s'])
                hits_per_car.append(st['impacts'])
                resp += st['respawns']
                stops += st['stops']
                kinds.update(st['impacts_by_kind'])
        # Spots: events binned by slice across every run and car, ranked by
        # an estimate of the seconds lost there (event_cost).
        bins = defaultdict(lambda: {'cost': 0.0, 'types': Counter(), 'runs': set(),
                                    'cars': set(), 'stall_s': 0.0, 'x': [], 'z': []})
        for r in runs:
            for e in r.get('events', []):
                b = bins[e['slice'] // a.bin]
                b['cost'] += event_cost(e)
                b['types'][e['type']] += 1
                b['runs'].add(r['name'])
                b['cars'].add((r['name'], e['car']))
                if e['type'] == 'stall':
                    b['stall_s'] += e['seconds']
                b['x'].append(e['x'])
                b['z'].append(e['z'])
        spots = sorted(bins.items(), key=lambda kv: kv[1]['cost'], reverse=True)
        spot_list = [{'slices': [k * a.bin, k * a.bin + a.bin - 1], 'cost': round(v['cost'], 1),
                      'runs': len(v['runs']), 'car_runs': len(v['cars']), 'types': dict(v['types']),
                      'stall_s': round(v['stall_s'], 1),
                      'x': round(statistics.mean(v['x'])), 'z': round(statistics.mean(v['z']))}
                     for k, v in spots[:a.top]]
        t = {'runs': len(runs), 'verdicts': dict(verdicts),
             'player_race_time_s': sorted(player_times), 'bot_race_time_s': sorted(bot_times),
             'impacts_per_car_run': round(statistics.mean(hits_per_car), 1) if hits_per_car else None,
             'impact_kinds': dict(kinds), 'respawns': resp, 'stops': stops, 'spots': spot_list}
        report['tracks'][track] = t

        def med(v):
            return f'{statistics.median(v):.1f}' if v else '-'
        md += [f'## {track}', '',
               f"runs {len(runs)} ({', '.join(f'{k} {v}' for k, v in verdicts.items())}); "
               f"race time player median {med(player_times)} s ({len(player_times)} finished), "
               f"bots median {med(bot_times)} s ({len(bot_times)} finished); "
               f"impacts per car per run {t['impacts_per_car_run']}, kinds {dict(kinds)}; "
               f"respawns {resp}, stops {stops}", '',
               '| slices | cost s | cars | stall s | episodes | x, z |', '|---|---|---|---|---|---|']
        for s in spot_list:
            md.append(f"| {s['slices'][0]}-{s['slices'][1]} | {s['cost']} | {s['car_runs']} | "
                      f"{s['stall_s']} | {', '.join(f'{k} {v}' for k, v in s['types'].items())} | "
                      f"{s['x']}, {s['z']} |")
        md.append('')
    (batch_dir / 'report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    (batch_dir / 'report.md').write_text('\n'.join(md), encoding='utf-8')
    return report


def batch_totals(batch_dir):
    """{track: totals over the runs of a batch} from the run summaries."""
    out = {}
    for path in sorted(Path(batch_dir).glob('*/summary.json')):
        r = json.loads(path.read_text(encoding='utf-8'))
        t = out.setdefault(r['job']['track'], {'player': [], 'bots': [], 'wall': 0, 'car': 0,
                                               'other': 0, 'stall_s': 0.0, 'respawns': 0,
                                               'cars': 0, 'unfinished': 0, 'verdicts': []})
        t['verdicts'].append(r['verdict'])
        for car, st in r.get('trace', {}).get('cars', {}).items():
            t['cars'] += 1
            if st.get('race_time_s') is None:
                t['unfinished'] += 1
            else:
                (t['player'] if int(car) == 0 else t['bots']).append(st['race_time_s'])
            kinds = st.get('impacts_by_kind', {})
            t['wall'] += kinds.get('wall', 0)
            t['car'] += kinds.get('car', 0)
            t['other'] += sum(v for k, v in kinds.items() if k not in ('wall', 'car'))
            t['stall_s'] += st.get('stall_seconds', 0.0)
            t['respawns'] += st.get('respawns', 0)
    return out


def compare(a):
    """Two batches side by side: B relative to A per track."""
    A, B = batch_totals(a.compare[0]), batch_totals(a.compare[1])

    def avg(v):
        return statistics.mean(v) if v else None

    def fmt(x):
        return '-' if x is None else f'{x:.1f}'
    say(f'{"track":10} {"player s A->B":>16} {"bots s A->B":>16} {"wall A->B":>11} '
        f'{"car A->B":>10} {"stall s A->B":>14} {"resp":>7} {"unfin":>7}')
    tot = Counter()
    for track in sorted(set(A) | set(B)):
        x, y = A.get(track), B.get(track)
        if not x or not y:
            say(f'{track:10} missing in one batch')
            continue
        say(f"{track:10} {fmt(avg(x['player'])):>7}->{fmt(avg(y['player'])):<8} "
            f"{fmt(avg(x['bots'])):>7}->{fmt(avg(y['bots'])):<8} "
            f"{x['wall']:>4}->{y['wall']:<5} {x['car']:>4}->{y['car']:<4} "
            f"{x['stall_s']:>6.0f}->{y['stall_s']:<6.0f} {x['respawns']:>2}->{y['respawns']:<3} "
            f"{x['unfinished']:>2}->{y['unfinished']:<3} {' '.join(y['verdicts'])}")
        for k in ('wall', 'car', 'respawns', 'unfinished'):
            tot[k + '_a'] += x[k]
            tot[k + '_b'] += y[k]
        tot['stall_a'] += x['stall_s']
        tot['stall_b'] += y['stall_s']
    say(f"total: wall {tot['wall_a']}->{tot['wall_b']}, car {tot['car_a']}->{tot['car_b']}, "
        f"stall {tot['stall_a']:.0f}->{tot['stall_b']:.0f} s, respawns {tot['respawns_a']}->"
        f"{tot['respawns_b']}, unfinished cars {tot['unfinished_a']}->{tot['unfinished_b']}")
    return 0


def reanalyze(a):
    """Re-read every run's dx11z_trace.csv of a batch and rebuild the report."""
    results = []
    for summary_path in sorted(a.reanalyze.glob('*/summary.json')):
        r = json.loads(summary_path.read_text(encoding='utf-8'))
        if a.laps is None:
            a.laps = next((int(x.split('=')[1]) - 1 for x in r.get('args', [])
                           if x.startswith('num_laps=')), None)
        S, H, R = read_trace(summary_path.parent / 'dx11z_trace.csv')
        tr = analyse_trace(S, H, R, a.laps, a.min_impact)
        r['trace'] = {k: v for k, v in tr.items() if k != 'events'}
        summary_path.write_text(json.dumps(r, indent=2), encoding='utf-8')
        (summary_path.parent / 'events.json').write_text(json.dumps(tr['events']), encoding='utf-8')
        r['events'] = tr['events']
        results.append(r)
    report = batch_report(results, a.reanalyze, a)
    for track, t in report['tracks'].items():
        say(f"{track}: player {t['player_race_time_s']} bots {t['bot_race_time_s'][:6]} "
            f"impacts/car {t['impacts_per_car_run']} respawns {t['respawns']} stalls {t['stops']}")
    say(f'report: {a.reanalyze / "report.md"}')
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--track', nargs='+', default=[None],
                    help='track file name(s): coastal, alps, aquatica... (default: fe.txt)')
    ap.add_argument('--car', nargs='+', default=[None],
                    help='CarModel name(s) for car[0]: 993, 959, 356a... (default: fe.txt)')
    ap.add_argument('--runs', type=int, default=1, help='races per track x car')
    ap.add_argument('--parallel', type=int, default=1, help='games at the same time')
    ap.add_argument('--seed', type=int, default=None,
                    help='rand_seed of the first run (+1 per further run)')
    ap.add_argument('--seconds', type=float, default=60, help='race time limit (simulation seconds)')
    ap.add_argument('--laps', type=int, default=None,
                    help='laps to time; the game gets one more, the run ends when every car did these')
    ap.add_argument('--opponents', type=int, default=None, help='AI opponents (default: fe.txt)')
    ap.add_argument('--opp-car', default=None, help='CarModel for the opponents')
    ap.add_argument('--spec', default=None,
                    help='physics for all cars: Simulation/CarData/<SPEC>.sim, e.g. GT1race, 550Arace')
    ap.add_argument('--aispec', default=None,
                    help='AI tuning for all cars: Simulation/AICarData/<AISPEC>.ais, e.g. Gt1, 550a')
    ap.add_argument('--no-aidrive', action='store_true', help='player car stays with the keyboard')
    ap.add_argument('--time-scale', type=float, default=1.0, help='[autotest] time_scale')
    ap.add_argument('--trace-every', type=int, default=4, help='trace period in ticks (64/s)')
    ap.add_argument('--full-render', action='store_true', help='keep the [display] section as it is')
    ap.add_argument('--show', action='store_true', help='keep the game windows on screen (default: minimized)')
    ap.add_argument('--ini', action='append', metavar='SECTION.KEY=VALUE',
                    help='override in the test dx11z.ini, e.g. --ini ai.lookahead_scale=0.6')
    ap.add_argument('--set', action='append', metavar='KEY=VALUE',
                    help='any fe.txt key, e.g. --set weather=1 --set traffic_density=2')
    ap.add_argument('--min-impact', type=float, default=0.0, help='ignore weaker impacts')
    ap.add_argument('--bin', type=int, default=10, help='slices per report spot')
    ap.add_argument('--top', type=int, default=12, help='spots per track in the report')
    ap.add_argument('--load-timeout', type=float, default=120)
    ap.add_argument('--hang-seconds', type=float, default=20)
    ap.add_argument('--stagger', type=float, default=3.0, help='seconds between launches')
    ap.add_argument('--keep-log', action='store_true', help='keep dx11z.log of passing runs')
    ap.add_argument('--out', type=Path, default=REPORTS)
    ap.add_argument('--reanalyze', type=Path, metavar='BATCH_DIR',
                    help='rebuild summaries and report of a finished batch from its traces')
    ap.add_argument('--compare', nargs=2, type=Path, metavar=('BATCH_A', 'BATCH_B'),
                    help='compare two finished batches track by track')
    a = ap.parse_args()
    if a.compare:
        return compare(a)
    if a.reanalyze:
        return reanalyze(a)
    missing = [f'track {t}' for t in a.track if t and not (GAME / 'GameData' / 'Track' / f'{t}.crp').exists()]
    missing += [f'car {c}' for c in a.car + [a.opp_car]
                if c and not (GAME / 'GameData' / 'CarModel' / f'{c}.crp').exists()]
    sim = GAME / 'GameData' / 'Simulation'
    if a.spec and not (sim / 'CarData' / f'{a.spec}.sim').exists():
        missing.append(f'spec {a.spec}')
    if a.aispec and not (sim / 'AICarData' / f'{a.aispec}.ais').exists():
        missing.append(f'aispec {a.aispec}')
    if missing:
        ap.error('not found in GameData (the game would silently fall back): ' + ', '.join(missing))

    jobs = []
    for track in a.track:
        for car in a.car:
            for k in range(a.runs):
                seed = a.seed + k if a.seed is not None else None
                jobs.append({'index': len(jobs), 'track': track, 'car': car, 'seed': seed})
    batch_dir = a.out / (datetime.datetime.now().strftime('%Y%m%d-%H%M%S') + '_batch')
    batch_dir.mkdir(parents=True, exist_ok=True)
    player_lst = PLAYER_LST.read_bytes() if PLAYER_LST.exists() else None
    say(f'{len(jobs)} runs, {a.parallel} at a time -> {batch_dir}')

    results = []
    try:
        with cf.ThreadPoolExecutor(max_workers=max(1, a.parallel)) as pool:
            futures = []
            for i, job in enumerate(jobs):
                futures.append(pool.submit(
                    lambda j=job, d=(i % max(1, a.parallel)) * a.stagger if i < a.parallel else 0:
                    (time.sleep(d), run_one(a, j, batch_dir, player_lst))[1]))
            for f in cf.as_completed(futures):
                results.append(f.result())
    except KeyboardInterrupt:
        subprocess.run(['taskkill', '/F', '/IM', TEST_EXE.name],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        say('interrupted')
    results.sort(key=lambda r: r['job']['index'])
    report = batch_report(results, batch_dir, a)
    say('\n=== batch ===')
    for r in results:
        say(f"{r['verdict']:4}  {r['name']:32} {'; '.join(r['fails'])}")
    for track, t in report['tracks'].items():
        say(f"{track}: player {t['player_race_time_s']} bots {t['bot_race_time_s'][:6]} "
            f"impacts/car {t['impacts_per_car_run']} respawns {t['respawns']} stops {t['stops']}")
    say(f'report: {batch_dir / "report.md"}')
    return 1 if any(r['verdict'] == 'FAIL' for r in results) else 0


if __name__ == '__main__':
    sys.exit(main())
