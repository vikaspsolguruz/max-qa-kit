"""Smoke driver: proves the generated harness works on an app. No AI, no cost.

  (run.py --driver) "python3 -I smoke_driver.py [mode ...]"

Default modes: fresh, relaunch. Each argument is a start mode name from the
app's profile (e.g. home, route:scriptList). After each start it waits,
captures, and writes $QA_OUT/smoke.json with screens, text counts and errors.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import step  # noqa: E402


def main(modes):
    out, results = os.environ["QA_OUT"], []
    for mode in modes:
        r = step.send({"action": "start", "mode": mode})
        if r.get("status") == "ok":
            r = step.send({"action": "wait", "ms": 1500})
        results.append({"mode": mode, "status": r.get("status"), "error": r.get("error"),
                        "app_exception": r.get("app_exception"), "screens": r.get("screens"),
                        "text_count": len(r.get("texts") or []), "texts": (r.get("texts") or [])[:12],
                        "screenshot": r.get("screenshot")})
        print(step.show(r)[:1500], flush=True)
    step.send({"action": "finish"}, wait_s=10)
    json.dump(results, open(os.path.join(out, "smoke.json"), "w"), indent=1)
    print(json.dumps([{k: x[k] for k in ("mode", "status", "screens", "text_count", "error")}
                      for x in results], indent=1))


if __name__ == "__main__":
    main(sys.argv[1:] or ["fresh", "relaunch"])
