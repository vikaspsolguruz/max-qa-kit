"""Compare two /qa reports of the same plan (e.g. iOS simulator vs Android phone).

  compare.py <report A dir> <report B dir> [--mode jev+claude]

Prints per scenario the verdicts, then every individual check whose verdict
differs, with both screenshots to open; and the accessibility lists
(no-label tappables, cut-off text) that differ. Exit 0 always (it reports).
"""
import json
import os
import sys


def load(d):
    r = json.load(open(os.path.join(d, "qa_report.json")))
    return {v["scenario"]: v for v in r["verdicts"]}


def checks(v, mode):
    m = (v.get("modes") or {}).get(mode) or {}
    out = {}
    for ci, cp in enumerate(m.get("checkpoint_results") or []):
        exps = v["checkpoints"][ci]["expectations"] if ci < len(v.get("checkpoints", [])) else []
        for p in cp.get("per") or []:
            i = p["i"] - 1
            out[(ci, exps[i] if i < len(exps) else f"#{p['i']}")] = (p["verdict"], cp.get("step"), p.get("why", ""))
    return m.get("verdict", v.get("verdict")), out


def main(a_dir, b_dir, mode="jev+claude"):
    A, B = load(a_dir), load(b_dir)
    same = total = 0
    for sid in sorted(set(A) | set(B)):
        if sid not in A or sid not in B:
            print(f"{sid}: only in {'A' if sid in A else 'B'}")
            continue
        va, ca = checks(A[sid], mode)
        vb, cb = checks(B[sid], mode)
        diffs = []
        for k in sorted(set(ca) | set(cb), key=lambda k: (k[0], k[1])):
            total += 1
            x, y = ca.get(k, ("missing", None, "")), cb.get(k, ("missing", None, ""))
            if x[0] == y[0]:
                same += 1
            else:
                diffs.append((k, x, y))
        print(f"{sid}: A {va} | B {vb} | checks same {len(ca) - len(diffs) if len(ca) == len(cb) else '?'}"
              f"/{max(len(ca), len(cb))}")
        for (ci, exp), x, y in diffs:
            print(f"   DIFF checkpoint {ci + 1} '{exp}': A {x[0]} (step {x[1]}) | B {y[0]} (step {y[1]})")
            for tag, z, d in (("A", x, a_dir), ("B", y, b_dir)):
                if z[2]:
                    print(f"      {tag} why: {z[2][:200]}")
                if z[1]:
                    print(f"      {tag} screenshot: {os.path.join(d, 'session', f'step_{z[1]:03d}.png')}")
        for key in ("no_label_tappables", "cut_off"):
            la = {json.dumps(t.get("icon") or t.get("label") or t, sort_keys=True) if isinstance(t, dict) else t
                  for t in A[sid].get(key) or []}
            lb = {json.dumps(t.get("icon") or t.get("label") or t, sort_keys=True) if isinstance(t, dict) else t
                  for t in B[sid].get(key) or []}
            if la != lb:
                print(f"   {key}: only A {sorted(la - lb)} | only B {sorted(lb - la)}")
    print(f"\nindividual checks with the same verdict: {same}/{total}")


if __name__ == "__main__":
    args = [x for x in sys.argv[1:] if not x.startswith("--")]
    if len(args) != 2:
        raise SystemExit(__doc__)
    mode = sys.argv[sys.argv.index("--mode") + 1] if "--mode" in sys.argv else "jev+claude"
    main(args[0], args[1], mode)
