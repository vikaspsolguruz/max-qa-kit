"""Triage a failed Flutter test / CI run.

Jev answers first, two ways (one choice question + yes/no checks with rules).
  both agree      -> trust Jev's label         (195/195 correct in testing)
  they disagree   -> ask Claude (Opus 5.5)     (100% on those in testing)
  Claude missing  -> needs a person

Usage (from anywhere):
  python3 -I triage.py --log test_output.log [--repo PATH]
        [--range origin/develop...HEAD | --diff-file change.diff]
        [--message "commit message"] [--no-claude] [--json]

Defaults: --repo = current dir; diff = uncommitted changes if any, else the
          last commit; --message = that commit's message. Pass --message to
          say what you intended: it helps tell bugs from intended changes.
Every run is appended to <state>/history/triage.jsonl (kit/state.py) for later review.
"""
import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import context as cx  # noqa: E402
import questions as q  # noqa: E402
import run_jev as jev  # noqa: E402
sys.path.insert(0, os.path.dirname(HERE))
import state as kit_state  # noqa: E402  (kit/state.py: .env and history/ location)

CLAUDE_MODEL = "claude-opus-5-5"

NEXT_STEP = {
    "real_bug": "Fix the app code. The test is right.",
    "outdated_test": "Update the test to the new, intended behavior.",
    "broken_test": "Fix the test that was added or edited. The app code is fine.",
    "compile_error": "Fix the build error shown in the log.",
    "dependency_break": "Adapt the code to the package change, or pin the old version.",
    "environment": "Fix the CI setup (secrets, SDK, tools, network), then rerun.",
    "flaky": "Rerun. If it fails again, quarantine the test and fix its timing/state.",
    "rendering_drift": "Check the golden diffs, then run `flutter test --update-goldens`.",
    "needs_human": "Jev was unsure and Claude was not used. A person should look.",
}


def git(repo, *a):
    r = subprocess.run(["git", "-C", repo, *a], capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit(f"git {' '.join(a)} failed: {r.stderr.strip()}")
    return r.stdout


def ask_claude(state):
    with tempfile.TemporaryDirectory() as empty:  # no project context
        r = subprocess.run(
            ["claude", "-p", "--model", CLAUDE_MODEL, "--safe-mode",
             "--tools", "", "--no-session-persistence", "--effort", "medium",
             "--output-format", "json", "--system-prompt", q.CLAUDE_SYSTEM],
            input=json.dumps(state, indent=1), cwd=empty, capture_output=True,
            text=True, timeout=600)
    events = json.loads(r.stdout)
    res = next(e for e in reversed(events) if e.get("type") == "result")
    if res.get("is_error"):
        raise RuntimeError(str(res.get("result"))[:200])
    ans = json.loads(re.search(r"\{.*\}", res["result"], re.S).group(0))
    if ans.get("label") not in q.LABEL_CRITERIA:
        raise RuntimeError(f"invalid label {ans.get('label')}")
    return ans, res["total_cost_usd"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--log", required=True, help="test/CI output file, or - for stdin")
    ap.add_argument("--repo", default=os.getcwd())
    ap.add_argument("--range", default="auto",
                    help="git range; auto = uncommitted changes if any, "
                         "else the last commit")
    ap.add_argument("--diff-file")
    ap.add_argument("--message")
    ap.add_argument("--no-claude", action="store_true")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--env-file", default=None,
                    help="file with TYPESAFE_API_KEY=; default $TYPESAFE_API_KEY or the kit's state .env")
    a = ap.parse_args()

    repo = os.path.abspath(a.repo)
    log = sys.stdin.read() if a.log == "-" else open(a.log).read()
    uncommitted = a.range == "auto" and bool(git(repo, "diff", "HEAD").strip())
    if a.diff_file:
        diff = open(a.diff_file).read()
    elif uncommitted:
        diff = git(repo, "diff", "HEAD")
    else:
        diff = git(repo, "diff", "HEAD~1..HEAD" if a.range == "auto" else a.range)
    message = a.message or ("(uncommitted local changes)" if uncommitted
                            else git(repo, "log", "-1", "--format=%B").strip())
    case = {"commit_message": message, "diff": diff,
            "log": cx.clean_log(log, repo)}
    state = cx.state(case, root=repo)

    t0 = time.time()
    resp = jev.ask({"model": jev.MODEL, "state": state,
                    "questions": q.QUESTIONS}, kit_state.jev_key(a.env_file))
    ans = resp["answers"]
    nouls = {k: v["noul"] for k, v in ans.items() if v["type"] == "noul"}
    choice = ans["cause"]["choice"]
    rules, rules_reason = q.decide(nouls, state["facts"])
    cost = resp["usage"]["input_tokens"] * jev.PRICE_PER_TOKEN

    if rules != "needs_human" and rules == choice:
        label, path, reason = rules, "jev", rules_reason
    elif a.no_claude:
        label, path, reason = "needs_human", "jev-unsure", \
            f"choice said {choice}, checks said {rules} ({rules_reason})"
    else:
        try:
            c, c_cost = ask_claude(state)
            label, path, reason = c["label"], "claude", c.get("reason", "")
            cost += c_cost
        except Exception as e:  # noqa: BLE001 - fall back to a person
            label, path, reason = "needs_human", "claude-failed", str(e)[:200]

    record = dict(time=datetime.now(timezone.utc).isoformat(), repo=repo,
                  label=label, decided_by=path, reason=reason,
                  next_step=NEXT_STEP[label], jev_choice=choice,
                  jev_rules=rules, nouls={k: round(v, 3) for k, v in nouls.items()},
                  facts=state["facts"], cost_usd=round(cost, 6),
                  seconds=round(time.time() - t0, 1),
                  commit_message=message.splitlines()[0][:120])
    os.makedirs(kit_state.history_dir(), exist_ok=True)
    with open(os.path.join(kit_state.history_dir(), "triage.jsonl"), "a") as f:
        f.write(json.dumps(record) + "\n")

    if a.json:
        print(json.dumps(record, indent=2))
        return
    who = {"jev": "Jev (both methods agreed)", "claude": "Claude (Jev was unsure)",
           "jev-unsure": "nobody: Jev was unsure", "claude-failed": "nobody: Claude failed"}
    print(f"\n  Cause:      {label}")
    print(f"  Next step:  {NEXT_STEP[label]}")
    print(f"  Why:        {reason}")
    print(f"  Decided by: {who[path]}   (${cost:.5f}, {record['seconds']}s)\n")


if __name__ == "__main__":
    main()
