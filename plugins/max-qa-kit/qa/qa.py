"""/qa pipeline: check a feature's spec scenarios on the running app.

  plan   one Claude call reads the spec and writes, per scenario: the start
         state, what to do, and small concrete expectations in the spec's
         own words (or why it can't be checked here).
  run    run.py session; agent.py drives each scenario with a capped Claude
         session over step.py, captures the screen and judges it.
  report written by agent.py: qa_report.json (also read by the coverage
         map as "AI-checked") and qa_report.md.

<app> = app name (kit/apps/<app>/profile.json), app folder, or profile path.
Both commands first refresh the profile from the app's code (setup.py; hand
notes are kept), so plans and the harness match today's code.

Usage:
  python3 -I qa.py plan <app> <feature> <out_dir> [--scenarios US1-AS1,US1-AS2]
  python3 -I qa.py run  <app> <out_dir> [--judge jev+claude|claude|both]
                        [--max-session 560] [--platform ios|android] [--device UDID|SERIAL]
                        [--replace-installed] [--dart-define DATA_SOURCE=api ...] [--dry-run]
  --platform android runs on the connected Android phone/emulator (default: the iOS simulator);
  --device picks one by simulator udid or adb serial; --dry-run prints the run.py command.
"""
import json
import os
import re
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
KIT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(KIT, "coverage"))
sys.path.insert(0, os.path.join(KIT, "harness"))
sys.path.insert(0, HERE)
import coverage  # noqa: E402
import generate  # noqa: E402
import app_profile as P  # noqa: E402

MODEL = "claude-opus-5-5"

PLAN_SYSTEM = """You plan QA checks for a mobile app, one per spec scenario.
For each requested scenario, write:
- given: the scenario's Given, in plain words, as the situation the app must be in
- start: {"mode": one of the app's start_modes, "options": {name: value} (only the
  options a mode lists), "variants": {switch: value}} using ONLY the app's listed
  switches (names from "switches"; old names in "switch_aliases" also work) and
  their values, to set up the Given
- goal: what the driver must do after the start, step by step in short words
- checkpoints: the moments to look at, in order. Each is
  {"when": "<the moment, e.g. right after Find customer opens>",
   "expectations": [small checks, ONE observable thing each, true at that moment]}.
  Use the spec's own words; when a functional requirement (FR-...) defines exact
  words or values, use them. Every check must be judgeable from THAT moment's
  screen alone: never compare with another moment ("same as before"); write the
  concrete thing each moment must show instead. Expect a specific name or value
  only if the app's test data shows it is on THAT screen (test_data_notes say which
  data a screen shows; test_data lists each fake-data file's values, and different
  lists are not the same data); otherwise expect the pattern (e.g. "a matching
  customer row appears"). Only include checks the set-up data will exercise;
  put conditional parts the data cannot trigger in not_checked.
  What a checkpoint can see: a screenshot; the visible texts (cut-off text marked);
  the accessibility tree (each element's label, value, hint and role: button, field,
  header, focused, checked, on/off, selected, disabled); every tappable WITHOUT a
  label (what a screen reader cannot name); and every tappable whose measured tap area
  is under 48x48 logical px. So screen-reader labels and touch-target sizes CAN be
  checked from the accessibility tree and these lists, without turning on a screen reader.
  It cannot see colours as numbers, sounds, haptics, or the system share sheet.
- not_checked: parts of the Then this plan cannot check, with the reason
- checkable: false when the harness cannot produce the Given or observe the Then
  at all (camera, real network, real payments, timing under load, a second device,
  data the listed variants cannot create); then give why_not
Reply with only JSON: {"scenarios": [{"id", "given", "start", "goal",
"checkpoints", "not_checked", "checkable", "why_not"}]}"""


def claude_json(system, prompt, budget):
    with tempfile.TemporaryDirectory() as empty:
        r = subprocess.run(
            ["claude", "-p", "--model", MODEL, "--safe-mode", "--tools", "",
             "--no-session-persistence", "--effort", "medium",
             "--max-budget-usd", str(budget),
             "--output-format", "json", "--system-prompt", system],
            input=prompt, cwd=empty, capture_output=True, text=True, timeout=600)
    events = json.loads(r.stdout)
    res = next(e for e in reversed(events) if e.get("type") == "result")
    if res.get("is_error"):
        raise RuntimeError(str(res.get("result"))[:300])
    return json.loads(re.search(r"\{.*\}", res["result"], re.S).group(0)), res["total_cost_usd"]


def plan(app_arg, feature, out, scenario_ids):
    prof = P.refresh(P.load(app_arg))
    app = P.app_path(prof)
    out = P.kit_state.out_dir(app, out)
    coverage.APP = app
    specs = coverage.read_specs()
    key = next((k for k in specs if k == feature or k[4:] == feature), None)
    if not key:
        raise SystemExit(f"no spec folder for {feature}")
    all_ids = [sid for sid, _ in specs[key]["scenarios"]]
    ids = scenario_ids or all_ids
    missing = [i for i in ids if i not in all_ids]
    if missing:
        raise SystemExit(f"not in {key}: {missing}")
    spec_text = open(os.path.join(app, "specs", key, "spec.md")).read()
    prompt = json.dumps({"app": P.app_view(prof),
                         "feature": key, "scenarios_to_plan": ids, "spec_md": spec_text})
    result, cost = claude_json(PLAN_SYSTEM, prompt, budget=2)
    got = {s["id"]: s for s in result["scenarios"]}
    os.makedirs(out, exist_ok=True)
    doc = {"feature": key, "app": os.path.relpath(prof["_path"], os.path.dirname(KIT)), "plan_cost_usd": cost,
           "scenarios": [got[i] for i in ids if i in got],
           "not_planned": [i for i in ids if i not in got]}
    json.dump(doc, open(os.path.join(out, "qa_plan.json"), "w"), indent=2)
    print(f"planned {len(doc['scenarios'])}/{len(ids)} scenarios of {key} "
          f"({sum(s.get('checkable', True) for s in doc['scenarios'])} checkable), "
          f"cost ${cost:.3f}")
    for s in doc["scenarios"]:
        print(f"  {s['id']:9} start={s['start']} | {s['goal'][:70]}")
        for c in s.get("checkpoints", []):
            print(f"    @ {c['when']}")
            for e in c["expectations"]:
                print(f"      - {e}")
        for n in s.get("not_checked", []) or []:
            print(f"    not checked: {str(n)[:110]}")
        if not s.get("checkable", True):
            print(f"      (not checkable: {s.get('why_not')})")


def run_command(prof, out, judge_mode, max_session, defines=(), device=None, platform=None,
                replace_installed=False):
    plat, dev = P.device(prof, device, platform)
    harness = os.path.join(out, "qa_harness.dart")  # kept with the results it produced
    driver = (f"python3 -I {os.path.join(HERE, 'agent.py')} {prof['_path']} "
              f"{os.path.abspath(out)} {judge_mode}")
    grant, problems = P.grants(prof, plat)
    cmd = ["python3", "-I", os.path.join(KIT, "harness", "run.py"),
           "--app", P.app_path(prof), "--device", dev, "--platform", plat, "--harness", harness,
           "--out", os.path.join(out, "session"), "--driver", driver,
           "--max-session", str(max_session), "--app-id", P.platform_id(prof, plat),
           *(["--grant", ",".join(grant)] if grant else []),
           *(["--replace-installed"] if replace_installed else []),
           *[x for d in defines for x in ("--dart-define", d)]]
    return cmd, harness, problems


def run(app_arg, out, judge_mode, max_session, defines=(), device=None, platform=None,
        replace_installed=False, dry=False):
    prof = P.refresh(P.load(app_arg))
    out = P.kit_state.out_dir(P.app_path(prof), out)
    os.makedirs(out, exist_ok=True)
    cmd, harness, problems = run_command(prof, out, judge_mode, max_session, defines, device,
                                         platform, replace_installed)
    if dry:
        print(" ".join(cmd))
        return
    open(harness, "w").write(generate.generate(prof))
    for problem in problems:
        print(f"note — app setup: {problem}")
    subprocess.run(cmd, check=False)


if __name__ == "__main__":
    a = sys.argv[1:]
    pos = [x for i, x in enumerate(a) if not x.startswith("--") and
           (i == 0 or a[i - 1] not in ("--scenarios", "--judge", "--max-session", "--dart-define",
                                       "--device", "--platform"))]
    if any(x in ("-h", "--help") for x in a) or not a or \
            (a[0] == "plan" and len(pos) < 4) or (a[0] == "run" and len(pos) < 3):
        raise SystemExit(__doc__)
    if a[0] == "plan":
        ids = a[a.index("--scenarios") + 1].split(",") if "--scenarios" in a else None
        plan(a[1], a[2], a[3], ids)
    elif a[0] == "run":
        judge = a[a.index("--judge") + 1] if "--judge" in a else "jev+claude"
        cap = int(a[a.index("--max-session") + 1]) if "--max-session" in a else 560
        defs = [a[i + 1] for i, x in enumerate(a) if x == "--dart-define"]
        dev = a[a.index("--device") + 1] if "--device" in a else None
        plat = a[a.index("--platform") + 1] if "--platform" in a else None
        if plat not in (None, "ios", "android"):
            raise SystemExit("--platform is ios or android")
        run(a[1], a[2], judge, cap, defs, dev, plat, "--replace-installed" in a, "--dry-run" in a)
    else:
        raise SystemExit(__doc__)
