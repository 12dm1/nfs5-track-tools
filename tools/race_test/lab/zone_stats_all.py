"""zone_stats_all.py BATCH LABEL : trouble totals of a multi-track batch, one line."""
import sys, glob, json, os, statistics
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
from learn_zones import spins
b, label = sys.argv[1], sys.argv[2]
tot = dict(wall=0, hit3=0, car=0, stall=0.0, spin=0, unfin=0)
bots = []
for f in glob.glob(b + '/*/summary.json'):
    s = json.load(open(f))
    for c, st in s['trace']['cars'].items():
        k = st.get('impacts_by_kind', {})
        tot['wall'] += k.get('wall', 0); tot['hit3'] += k.get('hit3', 0); tot['car'] += k.get('car', 0)
        if c != '0':
            tot['stall'] += st.get('stall_seconds', 0)
            if st.get('race_time_s') is not None:
                bots.append(st['race_time_s'])
        if st.get('race_time_s') is None:
            tot['unfin'] += 1
    tp = f.replace('summary.json', 'dx11z_trace.csv')
    if os.path.exists(tp):
        tot['spin'] += len(spins(tp))
print(f"{label}: tracks {len(glob.glob(b + '/*/summary.json'))} walls {tot['wall']} objects {tot['hit3']} "
      f"spins {tot['spin']} bot-stall {tot['stall']:.0f}s cars {tot['car']} unfinished {tot['unfin']} | "
      f"bot time sum {sum(bots):.0f}s")
