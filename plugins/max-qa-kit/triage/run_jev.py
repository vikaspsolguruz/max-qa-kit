"""Run cases through Jev. Saves the exact request + response per case.

Usage: python3 -I run_jev.py <cases_dir> <out_dir> <env_file> [split] [--force]
Resumable: cases that already have a result are skipped unless --force.
The API key is read from env_file and never printed.
"""
import concurrent.futures as cf
import glob
import json
import os
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import context as cx  # noqa: E402
import questions as q  # noqa: E402

MODEL = "jev-1.14.0"
URL = "https://api.typesafe.ai/v1/systemone"
PRICE_PER_TOKEN = 0.042 / 1e6


def api_key(env_file):
    for line in open(env_file):
        if line.startswith("TYPESAFE_API_KEY="):
            return line.split("=", 1)[1].strip()
    raise SystemExit("TYPESAFE_API_KEY missing")


def ask(payload, key, tries=4):
    for attempt in range(tries):
        req = urllib.request.Request(
            URL, data=json.dumps(payload).encode(), method="POST",
            headers={"Authorization": f"Bearer {key}",
                     "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=180) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503) and attempt < tries - 1:
                time.sleep(2 ** attempt * 3)
                continue
            # surface as an error, never as an answer
            raise RuntimeError(f"HTTP {e.code}: {e.read().decode()[:300]}")


def run_case(case, key, out_dir):
    st = cx.state(case)
    payload = {"model": MODEL, "state": st, "questions": q.QUESTIONS}
    t0 = time.time()
    resp = ask(payload, key)
    a = resp["answers"]
    nouls = {k: v["noul"] for k, v in a.items() if v["type"] == "noul"}
    label, reason = q.decide(nouls, st["facts"])
    res = dict(id=case["id"], model=resp["model"],
               choice=a["cause"]["choice"],
               choice_probs=a["cause"]["probabilities"],
               rules=label, rules_reason=reason, nouls=nouls,
               input_tokens=resp["usage"]["input_tokens"],
               cost_usd=resp["usage"]["input_tokens"] * PRICE_PER_TOKEN,
               seconds=round(time.time() - t0, 2),
               request=payload, response=resp)
    json.dump(res, open(os.path.join(out_dir, case["id"] + ".json"), "w"),
              indent=2)
    return res


def main():
    cases_dir, out_dir, env_file = (os.path.abspath(a) for a in sys.argv[1:4])
    split = next((a for a in sys.argv[4:] if not a.startswith("--")), None)
    force = "--force" in sys.argv
    os.makedirs(out_dir, exist_ok=True)
    key = api_key(env_file)
    cases = [json.load(open(f)) for f in sorted(glob.glob(f"{cases_dir}/*.json"))]
    todo = [c for c in cases if (not split or c["split"] == split) and
            (force or not os.path.exists(f"{out_dir}/{c['id']}.json"))]
    print(f"running {len(todo)} cases on {MODEL}")
    done = errors = 0
    with cf.ThreadPoolExecutor(4) as ex:
        futs = {ex.submit(run_case, c, key, out_dir): c["id"] for c in todo}
        for fu in cf.as_completed(futs):
            try:
                fu.result()
                done += 1
            except Exception as e:  # noqa: BLE001
                errors += 1
                print(f"  ERROR {futs[fu]}: {e}")
    print(f"done {done}, errors {errors}")


if __name__ == "__main__":
    main()
