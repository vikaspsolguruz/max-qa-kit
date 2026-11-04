"""Shared input for every contestant (Jev, Claude): the state for one case.

state = commit_message + diff + log + facts (exact, computed by code)
        + source (lines around the failing test / compile error locations,
          as the files looked after the developer's change)
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:  # the experiment's staged scenarios (scripts/scenarios.py); absent in the plugin
    import scenarios as sc  # noqa: E402
    _SCENARIOS = sc.S
except ImportError:
    _SCENARIOS = []

SKELTER = os.path.expanduser("~/StudioProjects/skelter")
LOC = re.compile(r"((?:integration_test|test|lib)/[\w/.-]+\.dart)(?::| )(\d+):\d+")
MIXED = {"M1": ["R2", "F3"], "M2": ["O2", "F1"], "M3": ["T3", "F2"],
         "M4": ["R7", "F4"]}
_BY_ID = {s["id"]: s for s in _SCENARIOS}


ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def clean_log(log, repo):
    """Strip terminal colors and make repo paths relative, like CI logs."""
    repo = os.path.abspath(repo)
    return (ANSI.sub("", log).replace(repo + "/", "").replace(repo, ".")
            .replace("file://", ""))


def post_change_files(base_id):
    """File contents after the scenario's change (only files it touched)."""
    files = {}
    for bid in MIXED.get(base_id, [base_id]):
        s = _BY_ID.get(bid)
        if not s:
            continue
        for rel, old, new in s.get("edits", []):
            cur = files.get(rel) or open(os.path.join(SKELTER, rel)).read()
            files[rel] = cur.replace(old, new)
        files.update(s.get("new_files", {}))
        files.update(s.get("hidden_files", {}))
    return files


def read_file(rel, overlay, root=SKELTER):
    if rel in overlay:
        return overlay[rel]
    path = os.path.join(root, rel)
    return open(path).read() if os.path.isfile(path) else None


def _snippet(rel, text, line, before, after):
    lines = text.splitlines()
    lo, hi = max(0, line - 1 - before), min(len(lines), line + after)
    return {"file": rel, "line": line,
            "code": "\n".join(f"{i + 1:4}  {lines[i]}" for i in range(lo, hi))}


def _line_of_test_name(text, name):
    """Line where the longest word-suffix of a test name appears quoted."""
    words = name.replace(" (variant: macOS)", "").split()
    for k in range(len(words), 0, -1):
        needle = " ".join(words[-k:])
        for q in ("'", '"'):
            i = text.find(q + needle + q)
            if i >= 0:
                return text.count("\n", 0, i) + 1
    return None


def source(case, max_snippets=3, root=SKELTER):
    overlay = post_change_files(case["base"]) if "base" in case else {}
    seen, out = [], []

    def add(rel, line, before, after):
        text = read_file(rel, overlay, root)
        if text is None or line is None or len(out) == max_snippets:
            return
        if any(r == rel and abs(l - line) <= 8 for r, l in seen):
            return
        seen.append((rel, line))
        out.append(_snippet(rel, text, line, before, after))

    # 1) failing tests reported by name (blocTest etc. give no line number)
    failing = re.findall(r"^(?:\S+Z )?\s+((?:integration_)?test/[\w/.-]+\.dart): (.+?)\s*$",
                         case["log"], re.M)  # indented location in a stack
    failing += re.findall(r"((?:integration_)?test/[\w/.-]+\.dart): (.+?) \[E\]\s*$",
                          case["log"], re.M)  # "[E]" line with a path
    for rel, name in failing:
        text = read_file(rel, overlay, root)
        if text:
            add(rel, _line_of_test_name(text, name), 2, 18)
    # 2) explicit file:line locations (compile errors, stack frames)
    for rel, line in LOC.findall(case["log"]):
        add(rel, int(line), 6, 6)
    return out


def facts(case):
    diff, log = case["diff"], case["log"]
    files = sorted({l[6:] for l in diff.splitlines() if l.startswith("+++ b/")})
    added = [l for l in diff.splitlines() if l.startswith("+") and
             not l.startswith("+++")]
    pct = [float(x) for x in re.findall(r"Pixel test failed, ([0-9.]+)%", log)]
    return {
        "files_changed": files,
        "dependency_files_changed": any(f in ("pubspec.yaml", "pubspec.lock")
                                        for f in files),
        "test_files_changed": any(f.startswith(("test/", "integration_test/")) for f in files),
        "flutter_sdk_version_changed": any("flutter-version:" in l for l in added),
        "tests_started": bool(re.search(r"(?m)(?:^|\d\d:\d\d )\s*\+\d+(?: ~\d+)?(?: -\d+)?: ", log)),
        "version_solving_failed": "version solving failed" in log,
        "compile_errors_in_log": bool(re.search(r"\.dart:\d+:\d+: Error:", log)),
        "test_order_shuffled": "randomize-ordering-seed" in log,
        "golden_image_failures": len(pct),
        "max_golden_pixel_diff_percent": max(pct) if pct else None,
        "failing_tests": sorted({m.strip() for m in
                                 re.findall(r"\+\d+(?: ~\d+)? -\d+: (.+?) \[E\]",
                                            log)})[:6],
    }


LOG_CAP, LOG_HEAD = 40000, 3000


def trim_log(log):
    """Same rule for every contestant: keep the job start and the end of the
    log (where CI failures are), drop the middle, and say so."""
    if len(log) <= LOG_CAP:
        return log
    head = log[:LOG_HEAD].rsplit("\n", 1)[0]
    tail = log[-(LOG_CAP - LOG_HEAD):].split("\n", 1)[1]
    dropped = log.count("\n") - head.count("\n") - tail.count("\n")
    return f"{head}\n[... {dropped} log lines trimmed ...]\n{tail}"


def state(case, root=SKELTER):
    return {
        "commit_message": case["commit_message"],
        "diff": case["diff"],
        "log": trim_log(case["log"]),
        "facts": facts(case),
        "source": source(case, root=root),
    }
