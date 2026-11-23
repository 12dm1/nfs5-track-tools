"""zone_stats.py BATCH LABEL : one line of trouble totals for a single-track batch."""
import sys, glob, json, math, os, statistics
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
from learn_zones import spins
b, label = sys.argv[1], sys.argv[2]
tot = dict(wall=0, hit3=0, car=0, stall=0.0, spin=0, unfin=0)
bots, player = [], []
for f in glob.glob(b + '/*/summary.json'):
    s = json.load(open(f))
    for c, st in s['trace']['cars'].items():
        k = st.get('impacts_by_kind', {})
        tot['wall'] += k.get('wall', 0); tot['hit3'] += k.get('hit3', 0); tot['car'] += k.get('car', 0)
        if c != '0':
            tot['stall'] += st.get('stall_seconds', 0)
        if st.get('race_time_s') is None:
            tot['unfin'] += 1
        elif c == '0':
            player.append(st['race_time_s'])
        else:
            bots.append(st['race_time_s'])
    tp = f.replace('summary.json', 'dx11z_trace.csv')
    if os.path.exists(tp):
        tot['spin'] += len(spins(tp))
print(f"{label}: runs {len(glob.glob(b + '/*/summary.json'))} walls {tot['wall']} objects {tot['hit3']} "
      f"spins {tot['spin']} bot-stall {tot['stall']:.0f}s cars {tot['car']} unfinished {tot['unfin']} | "
      f"bot time {statistics.mean(bots) if bots else 0:.1f}s player {statistics.mean(player) if player else 0:.1f}s")
