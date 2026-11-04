"""Driver for a /qa session (run.py starts it with QA_SERVER/QA_OUT set).

For each planned scenario:
  1. start the app in the planned state (fixtures reset, variants set)
  2. a capped Claude session performs the "When" through step.py only
  3. capture the screen and judge it against the expectations
Then write qa_report.json + qa_report.md.

Usage (via run.py): agent.py <profile.json> <out_dir> <judge_mode>
"""
import json
import os
import re
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
KIT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(KIT, "harness"))
sys.path.insert(0, os.path.join(KIT, "judge"))
sys.path.insert(0, HERE)
from step import send, show  # noqa: E402
import judge as J  # noqa: E402
import app_profile as P  # noqa: E402

STEP = f"python3 -I {os.path.join(KIT, 'harness', 'step.py')}"
DRIVE_BUDGET_USD = 0.6
DEVICE_WORDS = {"ios": "an iOS simulator", "android": "an Android device"}

DRIVER_SYSTEM = """You drive a Flutter app on {device} for one QA check.
The app is already started in the right starting state. Do the GOAL step by step,
then stop as soon as the screen should show the expected result. Do not judge
whether the app is right, and do not explore beyond the goal.

Run app steps ONLY with this exact command prefix (one step per command):
  {step} <action> ...
Each command prints the screen after the step: screens showing, visible text,
accessibility labels ([tap] = tappable), and tappable things with no label.
Actions:
  tap text "Exact text" | tap text_contains "part" | tap semantics "Exact label"
  tap semantics_contains "part" | tap icon U+XXXX | tap point X Y | tap type WidgetType
  long_press <same targets> | enter_text type TextFormField "text" (--index N)
  enter_text semantics "Field label" "text" | scroll down|up|left|right [pixels]
  scroll_to text "Exact text" | back | wait <ms> | capture
  variant <name> <value>   (fake-data switch; applies to the next data load)
  start relaunch           (reopen the app keeping what is saved on the phone)
  helper <name> [args ...] [option=value]   (one of the app's own test helpers listed
                           under "helpers"; a shortcut, e.g. helper openQuoteRow "Maria Santos";
                           after -- every word is an argument)
Add --index N after a target to pick the Nth match.
App notes: {notes}
The expected result has CHECKPOINTS: moments in the goal where the screen will be
checked. Each command's output starts with "step N". When the app is at a
checkpoint's moment, remember that step number (run `capture` first if the moment
comes right after a wait). Never do extra steps between a checkpoint's moment and
noting it.
If a checkpoint's moment cannot be reached, skip it and continue with the next ones
you can reach.
When done, reply with only JSON:
{{"status": "ready" | "partial" | "could_not_reach",
  "checkpoint_steps": [N, or null for a checkpoint you could not reach, for each checkpoint in order],
  "note": "<one short sentence; say why any checkpoint could not be reached>"}}
("ready" = every checkpoint reached, "partial" = some, "could_not_reach" = none.)"""


def drive(scenario, cfg):
    notes = json.dumps(P.driver_view(cfg))
    system = DRIVER_SYSTEM.format(step=STEP, notes=notes, device=DEVICE_WORDS.get(
        os.environ.get("QA_PLATFORM", "ios"), "an iOS simulator"))
    prompt = json.dumps({"given": scenario["given"], "goal": scenario["goal"],
                         "checkpoints": [{"n": i + 1, "when": c["when"]}
                                         for i, c in enumerate(scenario["checkpoints"])]})
    with tempfile.TemporaryDirectory() as empty:
        r = subprocess.run(
            ["claude", "-p", "--model", J.CLAUDE_MODEL, "--safe-mode",
             "--tools", "Bash", "--allowedTools", f"Bash({STEP}:*)",
             "--no-session-persistence", "--effort", "medium",
             "--max-budget-usd", str(DRIVE_BUDGET_USD),
             "--output-format", "json", "--system-prompt", system],
            input=prompt, cwd=empty, capture_output=True, text=True, timeout=400,
            env=dict(os.environ))
    try:
        events = json.loads(r.stdout)
    except json.JSONDecodeError:
        return {"status": "could_not_reach", "note": f"driver output unreadable: {r.stderr[:150]}"}, 0.0, 0
    res = next(e for e in reversed(events) if e.get("type") == "result")
    steps = sum(1 for e in events if e.get("type") == "assistant"
                for b in e.get("message", {}).get("content", []) if b.get("type") == "tool_use")
    m = re.search(r"\{.*\}", res.get("result") or "", re.S)
    ans = json.loads(m.group(0)) if m else {"status": "could_not_reach",
                                            "note": (res.get("result") or "no answer")[:150]}
    return ans, res.get("total_cost_usd", 0.0), steps


def judge_checkpoints(sc, steps_at, mode, key, cfg, rec):
    """Judge every checkpoint screen of one scenario with one judge mode."""
    session_dir = os.environ["QA_OUT"]
    per_cp, all_verdicts, cost, esc = [], [], 0.0, 0
    for i, cp in enumerate(sc["checkpoints"]):
        n = steps_at[i] if i < len(steps_at) else None
        cap_file = os.path.join(session_dir, f"step_{int(n):03d}.json") if n else None
        if not cap_file or not os.path.exists(cap_file):
            per_cp.append({"when": cp["when"], "verdict": "cannot_tell",
                           "why": (f"driver gave no usable step for this moment ({n})" if n else
                                   f"not reached: {(rec.get('drive') or {}).get('note', '')}")})
            all_verdicts.append("cannot_tell")
            continue
        # Judge each moment on its own terms: the scenario's Given describes
        # the end state and may not hold yet at earlier checkpoints.
        start = sc["start"]
        setup = (f"App started in mode '{start.get('mode', 'fresh')}' "
                 f"({P.describe_mode(cfg, start.get('mode', 'fresh'))})"
                 + (f" with options {start.get('options')}" if start.get("options") else "")
                 + (f" with fake-data switches {start.get('variants')}" if start.get("variants") else ""))
        check = {"id": f"{sc['id']}-cp{i + 1}", "expected": None,
                 "given": f"Moment: {cp['when']}. {setup}.",
                 "expectations": cp["expectations"], "capture": f"step_{int(n):03d}"}
        j = J.judge_check(check, session_dir, mode, key)
        cap = json.load(open(cap_file))
        per_cp.append({"when": cp["when"], "step": n, "verdict": j["verdict"], "per": j["per"],
                       "screenshot": cap.get("screenshot"), "screens": cap.get("screens")})
        all_verdicts += [p["verdict"] for p in j["per"]]
        cost += j["jev_cost"] + j["claude_cost"]
        esc += j["escalated"]
        for x in cap.get("no_label_tappables", []):
            if x not in rec.setdefault("no_label_tappables", []):
                rec["no_label_tappables"].append(x)
        for t in cap.get("texts", []):
            if t.endswith("[cut off]") and t not in rec.setdefault("cut_off", []):
                rec["cut_off"].append(t)
        if cap.get("app_exception"):
            rec["app_exception"] = cap["app_exception"]
    ov = J.overall(all_verdicts)
    return {"verdict": {"pass": "pass", "fail": "fail"}.get(ov, "cannot_tell"),
            "checkpoint_results": per_cp, "judge_cost_usd": cost, "escalated": esc}


def main():
    cfg_path, out, mode = sys.argv[1:4]
    cfg = P.load(cfg_path)
    plan = json.load(open(os.path.join(out, "qa_plan.json")))
    key = J.jev_key() if mode != "claude" else None
    assert mode in ("claude", "jev+claude", "both"), mode
    results = []
    for sc in plan["scenarios"]:
        t0 = time.time()
        rec = {"scenario": sc["id"], "given": sc["given"], "goal": sc["goal"],
               "checkpoints": sc.get("checkpoints", []), "not_checked": sc.get("not_checked")}
        start = None
        if not sc.get("checkable", True):
            rec.update(verdict="could_not_check", why=sc.get("why_not"))
        else:
            start = send({"action": "start", "mode": sc["start"].get("mode", "fresh"),
                          "options": sc["start"].get("options", {}),
                          "variants": sc["start"].get("variants", {})})
            if start.get("status") != "ok":
                rec.update(verdict="could_not_check", why=f"start failed: {start.get('error')}")
        if "verdict" not in rec:
            ans, drive_cost, steps = drive(sc, cfg)
            rec.update(drive=ans, drive_cost_usd=drive_cost, driver_tool_calls=steps)
            reached = [n for n in (ans.get("checkpoint_steps") or []) if n]
            if ans.get("status") not in ("ready", "partial") or not reached:
                rec.update(verdict="could_not_check", why=f"could not reach: {ans.get('note')}")
            else:
                steps_at = ans.get("checkpoint_steps") or []
                modes = ["claude", "jev+claude"] if mode == "both" else [mode]
                rec["modes"] = {m: judge_checkpoints(sc, steps_at, m, key, cfg, rec) for m in modes}
                main_mode = "jev+claude" if "jev+claude" in modes else modes[0]
                rec.update({k: v for k, v in rec["modes"][main_mode].items()})
        rec["seconds"] = round(time.time() - t0, 1)
        results.append(rec)
        print(f"{sc['id']:9} {rec['verdict']:15} {rec['seconds']:5}s "
              f"drive ${rec.get('drive_cost_usd', 0):.3f} judge ${rec.get('judge_cost_usd', 0):.3f}",
              flush=True)
        # saved after EVERY scenario (also could-not-check ones), so a run that is
        # stopped, or where nothing could be driven, still leaves its report
        json.dump({"spec": plan["feature"], "judge": mode, "verdicts": results},
                  open(os.path.join(out, "qa_report.json"), "w"), indent=2)
    write_md(plan, results, mode, out)


def write_md(plan, results, mode, out):
    n = {v: sum(r["verdict"] == v for r in results)
         for v in ("pass", "fail", "cannot_tell", "could_not_check")}
    cost = sum(r.get("drive_cost_usd", 0) + r.get("judge_cost_usd", 0) for r in results)
    L = [f"# QA report — {plan['feature']}", "",
         f"Judge: {mode}. Scenarios: {len(results)} — pass {n['pass']}, fail {n['fail']}, "
         f"can't tell {n['cannot_tell']}, could not check {n['could_not_check']}. "
         f"Cost ${cost + plan.get('plan_cost_usd', 0):.2f} (plan ${plan.get('plan_cost_usd', 0):.2f}).", ""]
    for r in results:
        L.append(f"## {r['scenario']} — {r['verdict'].upper()}")
        L.append(f"Given: {r['given']}  \nDid: {r['goal']}")
        for k, cp in enumerate(r.get("checkpoint_results", [])):
            L.append(f"- At: {cp['when']} (step {cp.get('step')}, {', '.join(cp.get('screens') or [])})")
            exps = r["checkpoints"][k]["expectations"]
            for p in cp.get("per", []):
                L.append(f"  - {p['verdict'] or '?'}: {exps[p['i'] - 1]}"
                         + (f" — {p['why']}" if p.get("why") else ""))
            if cp.get("why"):
                L.append(f"  - {cp['why']}")
            if cp.get("screenshot"):
                L.append(f"  - Screenshot: `{cp['screenshot']}`")
        if r.get("why"):
            L.append(f"- {r['why']}")
        note = (r.get("drive") or {}).get("note")
        if note:
            L.append(f"- Driver noticed: {note}")
        for n in r.get("not_checked") or []:
            L.append(f"- Not checked: {n}")
        L.append("")
    seen = {}
    for r in results:
        for x in r.get("no_label_tappables", []):
            seen.setdefault((x["icon"], x["where"]), r["scenario"])
    cut = sorted({t for r in results for t in r.get("cut_off", [])})
    exc = [(r["scenario"], r["app_exception"]) for r in results if r.get("app_exception")]
    L += ["## Other findings", ""]
    L += [f"- Tappable with no label (screen readers can't name it): icon {i} at {w} "
          f"(first seen in {s})" for (i, w), s in seen.items()] or ["- none"]
    L += [f"- Text cut off on screen: {t}" for t in cut]
    L += [f"- App exception in {s}: {e}" for s, e in exc]
    open(os.path.join(out, "qa_report.md"), "w").write("\n".join(L) + "\n")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--md-only":  # rebuild qa_report.md
        out = sys.argv[2]
        plan = json.load(open(os.path.join(out, "qa_plan.json")))
        rep = json.load(open(os.path.join(out, "qa_report.json")))
        write_md(plan, rep["verdicts"], rep["judge"], out)
    else:
        main()
