"""summary.py VAR [VAR...] : totals over seeds 100/200 per line variant."""
import sys, glob, json, statistics
import os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
import race_test as rt
R = os.path.join(rt.nfs5cfg.WORK, 'reports', 'race_test')
def load(var):
    out = {}
    for seed in (100, 200):
        bs = sorted(glob.glob(f'{R}/line_{var}/s{seed}/*_batch'))
        if var == 'B0' and seed == 100 and not bs:
            bs = sorted(glob.glob(f'{R}/lines_L0/*_batch'))
        if bs:
            out[seed] = rt.batch_totals(bs[-1])
    return out
data = {v: load(v) for v in sys.argv[1:]}
base = data[sys.argv[1]]
print(f'{"variant":8} {"seeds":5} {"walls":>6} {"cars":>5} {"stall s":>8} {"unfin":>5} {"bot time vs "+sys.argv[1]:>16} {"player vs":>10}')
for v, d in data.items():
    walls = cars = unfin = 0; stall = 0.0; rb = []; rp = []
    for seed, t in d.items():
        for tr, x in t.items():
            walls += x['wall']; cars += x['car']; stall += x['stall_s']; unfin += x['unfinished']
            b = base.get(seed, {}).get(tr)
            if b and x['bots'] and b['bots']:
                rb.append(statistics.mean(x['bots']) / statistics.mean(b['bots']))
            if b and x['player'] and b['player']:
                rp.append(x['player'][0] / b['player'][0])
    print(f'{v:8} {len(d):5} {walls:6} {cars:5} {stall:8.0f} {unfin:5} {(statistics.mean(rb)-1)*100:+15.1f}% {(statistics.mean(rp)-1)*100 if rp else 0:+9.1f}%')
