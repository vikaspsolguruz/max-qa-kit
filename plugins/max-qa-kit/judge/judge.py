"""Screen judge: does a captured screen meet a spec scenario's expectations?

Modes
  claude      Claude judges every expectation from screen text + screenshot.
  jev+claude  Jev judges each expectation twice (yes/no + shown/contradicted/
              can't-tell-from-text). Both agree -> that verdict. Otherwise only
              that expectation goes to Claude with the screenshot.

A check passes when every expectation passes, fails when any fails, and is
"cannot_tell" otherwise.

Usage: python3 -I judge.py eval <labels.json> <fixtures_dir> <out_dir> <mode>
                                 [--env-file .env] [--only P1,F2]
"""
import concurrent.futures as cf
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import state as kit_state  # noqa: E402

JEV_MODEL = "jev-1.14.0"
JEV_URL = "https://api.typesafe.ai/v1/systemone"
JEV_PRICE = 0.042 / 1e6
CLAUDE_MODEL = "claude-opus-5-5"
YES, NO = 0.7, 0.3

NOTES = ("Text ending in [cut off] is truncated on screen with an ellipsis. "
         "Lines in `accessibility` starting with [tap] can be tapped. "
         "Icon-only buttons without a label do not appear in the text; they are listed in "
         "tappable_without_label (a screen reader has nothing to announce for them). "
         "tap_area_under_48x48 lists every tappable whose measured tap area (logical px, "
         "Material tap padding included) is smaller than 48x48; one not listed there is at "
         "least 48x48. "
         "keyboard_requested true means the app asked for the on-screen keyboard; "
         f"the {'device' if os.environ.get('QA_PLATFORM') == 'android' else 'simulator'} may keep it "
         "hidden, so treat that as the keyboard opening.")


def screen_state(cap):
    t = cap.get("local_time", "")
    h = int(t[:2]) if re.match(r"\d\d:", t) else None
    tod = None if h is None else ("morning" if h < 12 else "afternoon" if h < 17 else "evening")
    return {
        "screens_showing": cap.get("screens", []),
        "visible_text": cap.get("texts", []),
        "accessibility": cap.get("semantics", []),
        "tappable_without_label": cap.get("no_label_tappables", []),
        "tap_area_under_48x48": cap.get("small_tappables", []),
        "focused": cap.get("focused"),
        "keyboard_requested": cap.get("keyboard_requested"),
        "facts": {"local_time": t, "time_of_day": tod},
        "notes": NOTES,
    }


# ------------------------------------------------------------------ Jev

def jev_questions(expectations):
    q = {}
    for i, e in enumerate(expectations, 1):
        q[f"e{i}_yes"] = {
            "type": "noul",
            "instructions": f"Does `screen` show this: {e}",
            "criteria": {
                "true": "The visible text or accessibility labels in `screen` clearly show it.",
                "false": "`screen` shows something else in its place, such as an error "
                         "message, an empty state, a different step, or text marked [cut off].",
            },
        }
        q[f"e{i}_textual"] = {
            "type": "noul",
            "instructions": f"Can a person check this from the screen's words alone, "
                            f"without seeing the picture: {e}",
            "criteria": {
                "true": "It is about words, names, numbers, or messages that appear as text.",
                "false": "It is about how something looks or where it is: size, color, "
                         "position, icons, a button that may have no text, or something "
                         "being visually dominant.",
            },
        }
        q[f"e{i}_how"] = {
            "type": "choice",
            "instructions": f"Compare `screen` with this statement: {e}",
            "criteria": {
                "shown": "The visible text or accessibility labels show the statement is true.",
                "contradicted": "The visible text shows something that makes the statement "
                                "false, such as an error message, an empty state, a different "
                                "step, or text marked [cut off].",
                "cannot_tell_from_text": "The statement is about something text cannot "
                                         "show, such as an icon-only button, a color, a size "
                                         "or layout, or the text says nothing about it.",
            },
        }
    return q


def jev_key(env_file=None):
    """$TYPESAFE_API_KEY, else the given .env, else the kit's state .env (kit/state.py)."""
    return kit_state.jev_key(env_file)


def jev_judge(state, expectations, key, given=None):
    payload = {"model": JEV_MODEL,
               "state": {"situation": given or "not stated", "screen": state},
               "questions": jev_questions(expectations)}
    for attempt in range(4):
        req = urllib.request.Request(JEV_URL, data=json.dumps(payload).encode(),
                                     method="POST", headers={
                                         "Authorization": f"Bearer {key}",
                                         "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                resp = json.load(r)
            break
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503) and attempt < 3:
                time.sleep(3 * 2 ** attempt)
                continue
            raise RuntimeError(f"Jev HTTP {e.code}: {e.read().decode()[:200]}")
    a = resp["answers"]
    out = []
    for i in range(1, len(expectations) + 1):
        yes, how = a[f"e{i}_yes"]["noul"], a[f"e{i}_how"]["choice"]
        textual = a[f"e{i}_textual"]["noul"]
        verdict = None
        if textual >= YES:  # only judge from text what text can show
            # Jev settles passes only; a possible fail always goes to Claude with
            # the screenshot (calibration: Jev called "OWNER" vs "Owner" and an
            # error screen with no rows a fail on clean builds).
            verdict = "pass" if yes >= YES and how == "shown" else None
        out.append({"i": i, "yes": round(yes, 3), "how": how, "textual": round(textual, 3),
                    "verdict": verdict, "by": "jev" if verdict else None})
    cost = resp["usage"]["input_tokens"] * JEV_PRICE
    return out, cost, payload


# ------------------------------------------------------------------ Claude

CLAUDE_SYSTEM = (
    "You are a QA tester checking one screen of a mobile app against expectations "
    "taken from the product spec. `situation` says what was set up and done before "
    "this screen; if the screen does not match it, the expectation fails. You get "
    "the screen's visible text, its "
    "accessibility labels, and the path of a screenshot. Open the screenshot with "
    "the Read tool and look at it before judging. For each expectation answer "
    "pass, fail, or cannot_tell, judging only what this screen shows. " + NOTES +
    ' Reply with only JSON: {"results": [{"i": <number>, "verdict": '
    '"pass|fail|cannot_tell", "why": "<one short sentence>"}]}'
)


def claude_judge(state, screenshot, expectations, idxs, given=None):
    """idxs: 1-based expectation numbers to judge."""
    items = [{"i": i, "expectation": expectations[i - 1]} for i in idxs]
    prompt = json.dumps({"situation": given or "not stated", "screen": state,
                         "screenshot": screenshot,
                         "expectations": items}, indent=1)
    with tempfile.TemporaryDirectory() as empty:
        r = subprocess.run(
            ["claude", "-p", "--model", CLAUDE_MODEL, "--safe-mode",
             "--tools", "Read", "--allowedTools", "Read",
             "--add-dir", os.path.dirname(screenshot),
             "--no-session-persistence", "--effort", "medium",
             "--output-format", "json", "--system-prompt", CLAUDE_SYSTEM],
            input=prompt, cwd=empty, capture_output=True, text=True, timeout=600)
    events = json.loads(r.stdout)
    res = next(e for e in reversed(events) if e.get("type") == "result")
    if res.get("is_error"):
        raise RuntimeError(str(res.get("result"))[:200])
    looked = any(b.get("type") == "tool_use" and b.get("name") == "Read"
                 and screenshot in json.dumps(b.get("input", {}))
                 for e in events if e.get("type") == "assistant"
                 for b in e.get("message", {}).get("content", []))
    ans = json.loads(re.search(r"\{.*\}", res["result"], re.S).group(0))
    got = {x["i"]: x for x in ans["results"]}
    out = [{"i": i, "verdict": got.get(i, {}).get("verdict", "cannot_tell"),
            "why": got.get(i, {}).get("why", "no answer"), "by": "claude"} for i in idxs]
    return out, res["total_cost_usd"], looked


# ------------------------------------------------------------------ eval

def overall(verdicts):
    if any(v == "fail" for v in verdicts):
        return "fail"
    return "pass" if verdicts and all(v == "pass" for v in verdicts) else "cannot_tell"


def judge_check(check, fixtures, mode, key):
    cap = json.load(open(os.path.join(fixtures, check["capture"] + ".json")))
    state, shot, exps = screen_state(cap), cap["screenshot"], check["expectations"]
    t0, jev_cost, claude_cost, looked = time.time(), 0.0, 0.0, None
    if mode == "claude":
        per, claude_cost, looked = claude_judge(state, shot, exps, range(1, len(exps) + 1),
                                                check.get("given"))
    else:
        per, jev_cost, _ = jev_judge(state, exps, key, check.get("given"))
        unsure = [p["i"] for p in per if p["verdict"] is None]
        if unsure:
            c, claude_cost, looked = claude_judge(state, shot, exps, unsure, check.get("given"))
            byi = {x["i"]: x for x in c}
            per = [dict(p, **byi[p["i"]]) if p["i"] in byi else p for p in per]
    return {"id": check["id"], "expected": check["expected"],
            "verdict": overall([p["verdict"] for p in per]), "per": per,
            "expectations": exps, "jev_cost": jev_cost, "claude_cost": claude_cost,
            "claude_looked_at_screenshot": looked,
            "escalated": sum(p["by"] == "claude" for p in per) if mode != "claude" else len(per),
            "seconds": round(time.time() - t0, 1)}


def main():
    _, cmd, labels, fixtures, out, mode, *rest = sys.argv
    assert cmd == "eval" and mode in ("claude", "jev+claude")
    env = rest[rest.index("--env-file") + 1] if "--env-file" in rest else None
    only = set(rest[rest.index("--only") + 1].split(",")) if "--only" in rest else None
    checks = [c for c in json.load(open(labels)) if not only or c["id"] in only]
    key = jev_key(env) if mode != "claude" else None
    od = os.path.join(out, mode.replace("+", "_"))
    os.makedirs(od, exist_ok=True)
    todo = [c for c in checks if not os.path.exists(os.path.join(od, c["id"] + ".json"))]
    with cf.ThreadPoolExecutor(4) as ex:
        for c, fu in [(c, ex.submit(judge_check, c, fixtures, mode, key)) for c in todo]:
            try:
                json.dump(fu.result(), open(os.path.join(od, c["id"] + ".json"), "w"), indent=2)
            except Exception as e:  # noqa: BLE001 - surface, never count as an answer
                print(f"ERROR {c['id']}: {e}")
    rows = [json.load(open(os.path.join(od, c["id"] + ".json")))
            for c in checks if os.path.exists(os.path.join(od, c["id"] + ".json"))]
    print(f"{'id':3} {'expected':8} {'verdict':11} esc  looked  per-expectation")
    for r in rows:
        mark = "✓" if r["verdict"] == r["expected"] else "✗"
        per = " ".join(f"{p['i']}:{(p['verdict'] or '?')[0]}{'*' if p['by'] == 'claude' else ''}"
                       for p in r["per"])
        print(f"{r['id']:3} {r['expected']:8} {mark} {r['verdict']:9} {r['escalated']:3}  "
              f"{str(r['claude_looked_at_screenshot']):6}  {per}")
    ok = sum(r["verdict"] == r["expected"] for r in rows)
    print(f"\n{mode}: {ok}/{len(rows)} checks correct; expectations escalated to Claude: "
          f"{sum(r['escalated'] for r in rows)}/{sum(len(r['per']) for r in rows)}; "
          f"cost: Jev ${sum(r['jev_cost'] for r in rows):.5f} + Claude "
          f"${sum(r['claude_cost'] for r in rows):.3f}")


if __name__ == "__main__":
    main()
