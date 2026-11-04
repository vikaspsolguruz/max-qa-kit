"""Coverage map: which spec scenarios do the integration tests check?

Reads a Max Architecture app with SDD specs (specs/NNN-name/spec.md) and
integration tests (integration_test/**/*_test.dart). Read-only.

Scenario status
  scenario-tested   a test names the exact scenario: "US1-AS3: ..." or a
                    story test with "(Sc.N)" / "(Sc.A-B)" / "(Sc.A,B,C)".
                    Sc.N = the Nth acceptance scenario in spec order
                    (checked: all Sc. references land in the named story).
  story-tested      a test names only the story ("US1: ...", "US1b: ...",
                    "US1/US2: ..."), so it is unknown which scenarios it checks.
  not covered       no test names it.
Specs whose scenarios have no IDs (plain "1. Given ... then ...") get
synthetic IDs AS1..ASn; any paired test counts as story-tested for them.

Usage: python3 -I coverage.py <app_dir> <out_dir> [--qa-reports DIR]
"""
import glob
import html
import json
import os
import re
import sys

APP = OUT = QA_DIR = None  # set in main(), or by an importer (e.g. kit/qa/qa.py)

SKIP_DIRS = {"examples"}  # template sample tests, not app coverage
TEST_NAME = re.compile(r"(?:testWidgets|patrolTest)\(\s*'((?:[^'\\]|\\.)*)'")


STORY_HEAD = re.compile(r"^#{2,4}\s+(?:User Story\s+(\d+)|US(\d+))\b.*$", re.M)
NUMBERED = re.compile(r"^\d+\.\s+(.+)$", re.M)


def _numbered_after_acceptance(block):
    """Numbered items that follow an 'Acceptance scenarios' line in a block."""
    m = re.search(r"acceptance scenarios", block, re.I)
    if not m:
        return []
    rest = block[m.end():]
    stop = re.search(r"^(#{2,4}\s|\*\*[A-Z][^*]*\*\*:?\s*$)", rest, re.M)
    return [x.strip() for x in NUMBERED.findall(rest[:stop.start()] if stop else rest)]


def read_specs():
    """Scenario list per spec. Three formats exist in our SDD specs:
    explicit IDs (**US1-AS1**), numbered lists without IDs (per story or one
    list), and stories with no scenario list at all (rows per story)."""
    specs = {}
    for f in sorted(glob.glob(os.path.join(APP, "specs", "*", "spec.md"))):
        key = os.path.basename(os.path.dirname(f))
        text = open(f).read()
        scen = [(m.group(1), m.group(2).strip())
                for m in re.finditer(r"\*\*(US\d+-AS\d+)\*\*:?\s*(.*)", text)]
        kind = "ids"
        if not scen:
            heads = list(STORY_HEAD.finditer(text))
            for i, h in enumerate(heads):
                story = h.group(1) or h.group(2)
                block = text[h.end():heads[i + 1].start() if i + 1 < len(heads) else len(text)]
                for k, item in enumerate(_numbered_after_acceptance(block), 1):
                    scen.append((f"US{story}-AS{k}", item))
            kind = "numbered"
            if not scen:
                scen = [(f"AS{k}", item) for k, item in
                        enumerate(_numbered_after_acceptance(text), 1)]
            if not scen and heads:
                scen = [(f"US{h.group(1) or h.group(2)}", h.group(0).lstrip("# ").strip())
                        for h in heads]
                kind = "stories-only"
            if not scen:
                kind = "unparsed"  # reported, never a silent 0
        docs = "\n".join(open(d).read() for d in
                         glob.glob(os.path.join(os.path.dirname(f), "*.md")))
        filters = {f for f in re.findall(r"--(?:plain-)?name[ =]\\?[\"']([^\"'\\]+)", docs)
                   # keep only filters with real words: "US2: a matching phone" names
                   # one test; bare IDs like "US1" or "US1-AS([89]|1[0-4])" match
                   # tests in every feature
                   if len(re.sub(r"US\d+[a-z]?(-AS[\w()|\[\]-]*)?:?", " ", f).split()) >= 2}
        specs[key] = {"scenarios": scen, "kind": kind, "docs": docs,
                      "name_filters": filters}
    return specs


def named_by_docs(test_file, specs):
    """The one spec whose docs name this test file (full path first, then file name)."""
    rel = os.path.relpath(test_file, APP)
    base = os.path.basename(test_file)
    for pat in (re.escape(rel), r"(?<![\w.\-/])" + re.escape(base)):
        ks = [k for k, v in specs.items() if re.search(pat, v["docs"])]
        if len(ks) == 1:
            return ks[0]
    return None


def spec_for(test_file, specs):
    """Spec of a test FILE: its file or folder name is the spec's slug
    (find_customer -> 006-find-customer), else the one spec whose docs name it."""
    rel = os.path.relpath(test_file, os.path.join(APP, "integration_test"))
    folder = rel.split(os.sep)[0] if os.sep in rel else ""
    stem = os.path.basename(test_file)[:-len("_test.dart")]
    names = {k[4:]: k for k in specs}  # "find-customer" -> "006-find-customer"
    for cand in (stem, folder):
        if cand.replace("_", "-") in names:
            return names[cand.replace("_", "-")]
    return named_by_docs(test_file, specs)


def file_specs(files, specs):
    """spec_for per file; a file still without one takes the spec its folder-mates
    agree on (e.g. login/login_buttons_test.dart next to login/login_test.dart)."""
    got = {f: spec_for(f, specs) for f in files}
    for f, sp in got.items():
        folder = os.path.dirname(f)
        if sp or folder == os.path.join(APP, "integration_test"):
            continue
        mates = {got[g] for g in files if g != f and os.path.dirname(g) == folder and got[g]}
        if len(mates) == 1:
            got[f] = mates.pop()
    return got


def sc_numbers(name):
    m = re.search(r"\(Sc\.\s*([\d,\s\-–]+)", name)
    if not m:
        return []
    nums = []
    for part in re.split(r"[,\s]+", m.group(1).strip()):
        if re.fullmatch(r"\d+[-–]\d+", part):
            a, b = map(int, re.split("[-–]", part))
            nums += range(a, b + 1)
        elif part.isdigit():
            nums.append(int(part))
    return nums


def read_tests(specs):
    tests, unpaired = [], []
    files = [f for f in sorted(glob.glob(os.path.join(APP, "integration_test", "**", "*_test.dart"),
                                         recursive=True))
             if os.path.relpath(f, APP).split(os.sep)[1] not in SKIP_DIRS]
    spec_of = file_specs(files, specs)
    for f in files:
        rel = os.path.relpath(f, APP)
        names = TEST_NAME.findall(open(f).read())
        if not names:
            continue
        by_file = spec_of[f]
        for n in names:
            # 1) the spec ran this test by name: `flutter test ... --name "<prefix>"`
            #    (--name matches any test whose name contains the text)
            hits = [(len(pat), k) for k, v in specs.items() for pat in v["name_filters"]
                    if pat and pat in n]
            named_in = [k for k, v in specs.items() if n in v["docs"]]
            if hits:
                best = max(hits)[0]
                ks = sorted({k for ln, k in hits if ln == best})
                sp, how = (ks[0], "spec ran it with --name") if len(ks) == 1 else \
                          (None, f"ambiguous --name: {ks}")
            elif len(named_in) == 1:
                sp, how = named_in[0], "named in spec docs"
            elif by_file in named_in or (by_file and not named_in):
                sp, how = by_file, "file/folder name"
            else:
                sp, how = None, (f"ambiguous: {named_in}" if named_in else "no matching spec")
            if sp is None:
                unpaired.append({"file": rel, "tests": [n], "why": how})
            else:
                tests.append({"file": rel, "spec": sp, "name": n, "paired_by": how})
    return tests, unpaired


def link(tests, specs):
    """Attach every test to scenarios or stories; return per-spec maps."""
    problems = []
    for t in tests:
        sp = specs[t["spec"]]
        order = [sid for sid, _ in sp["scenarios"]]
        t["scenarios"], t["stories"] = [], []
        exact = re.match(r"(US\d+-AS\d+)\b", t["name"])
        stories = re.match(r"((?:US\d+[a-z]?)(?:/US\d+[a-z]?)*)\s*:", t["name"])
        if exact:
            t["scenarios"] = [exact.group(1)]
        elif stories:
            t["stories"] = sorted({re.sub(r"[a-z]$", "", s)
                                   for s in stories.group(1).split("/")})
            for n in sc_numbers(t["name"]):
                if n <= len(order):
                    sid = order[n - 1]
                    if not any(sid.startswith(s + "-") for s in t["stories"]):
                        problems.append(f"{t['file']}: '{t['name'][:50]}' Sc.{n}"
                                        f" -> {sid} is outside {t['stories']}")
                    t["scenarios"].append(sid)
                else:
                    problems.append(f"{t['file']}: Sc.{n} beyond {len(order)} scenarios")
        for sid in t["scenarios"]:
            if sid not in order:
                problems.append(f"{t['file']}: '{sid}' not in {t['spec']}")
    return problems


def qa_checked():
    """Scenarios AI-checked by /qa reports (step 3.5): {(spec, id): report}."""
    out = {}
    if QA_DIR and os.path.isdir(QA_DIR):
        for f in glob.glob(os.path.join(QA_DIR, "**", "qa_report.json"), recursive=True):
            r = json.load(open(f))
            for v in r.get("verdicts", []):
                if v.get("verdict") in ("pass", "fail"):
                    out[(r["spec"], v["scenario"])] = os.path.relpath(f, OUT)
    return out


def build(specs, tests):
    ai = qa_checked()
    rows = []
    for key, sp in specs.items():
        mine = [t for t in tests if t["spec"] == key]
        for sid, text in sp["scenarios"]:
            story = sid.split("-")[0] if sid.startswith("US") else None
            exact = [t["name"] for t in mine if sid in t["scenarios"]]
            broad = [t["name"] for t in mine if not t["scenarios"] and
                     (story is None or story in t["stories"])]
            # strongest evidence first: an exact automated test, then an AI check
            # of this very scenario, then a test that only names the story
            if exact:
                status, by = "scenario-tested", exact
            elif (key, sid) in ai:
                status, by = "ai-checked", [f"AI: {ai[(key, sid)]}"] + broad
            elif broad:
                status, by = "story-tested", broad
            else:
                status, by = "not covered", []
            if (key, sid) in ai and status == "scenario-tested":
                by = by + [f"AI: {ai[(key, sid)]}"]
            rows.append({"spec": key, "id": sid, "text": text, "status": status,
                         "tests": by, "kind": sp["kind"]})
    return rows


STATUS_ORDER = ["scenario-tested", "ai-checked", "story-tested", "not covered"]


def write_html(rows, unlinked, unpaired, path):
    by_spec = {}
    for r in rows:
        by_spec.setdefault(r["spec"], []).append(r)
    total = {s: sum(r["status"] == s for r in rows) for s in STATUS_ORDER}
    parts = [f"<h1>Spec coverage</h1><p>{len(rows)} scenarios · " +
             " · ".join(f"<b>{v}</b> {k}" for k, v in total.items()) + "</p>"]
    for spec, rs in by_spec.items():
        c = {s: sum(r["status"] == s for r in rs) for s in STATUS_ORDER}
        note = {"ids": "", "unparsed": "", "numbered": " (spec has no scenario IDs; numbered here)",
                "stories-only": " (spec has no acceptance scenarios; one row per user story)"}[rs[0]["kind"]]
        parts.append(f"<details><summary><b>{spec}</b> — {len(rs)} scenarios: "
                     + ", ".join(f"{v} {k}" for k, v in c.items() if v) +
                     f"{note}</summary><table>")
        for r in rs:
            parts.append(f"<tr class='{r['status'].replace(' ', '-')}'><td>{r['id']}</td>"
                         f"<td>{r['status']}</td><td>{html.escape(r['text'][:160])}</td>"
                         f"<td>{'<br>'.join(html.escape(t[:90]) for t in r['tests'])}</td></tr>")
        parts.append("</table></details>")
    if unlinked or unpaired:
        parts.append("<h2>Tests not linked to a scenario</h2><ul>" +
                     "".join(f"<li>{html.escape(t['file'])}: {html.escape(t['name'][:90])}</li>"
                             for t in unlinked) +
                     "".join(f"<li>{html.escape(u['file'])}: {html.escape(', '.join(x[:60] for x in u['tests']))} — {html.escape(u.get('why', 'no matching spec'))}</li>"
                             for u in unpaired) + "</ul>")
    style = """<style>body{font:14px system-ui;margin:16px;max-width:1100px}
    table{border-collapse:collapse;width:100%;margin:6px 0 14px}td{border-bottom:1px solid #ddd;
    padding:4px 6px;vertical-align:top}.scenario-tested td:nth-child(2){color:#137333}
    .story-tested td:nth-child(2){color:#b06000}.ai-checked td:nth-child(2){color:#1a5fb4}
    .not-covered td:nth-child(2){color:#c5221f}summary{cursor:pointer;margin:4px 0}</style>"""
    open(path, "w").write(f"<!doctype html><meta charset=utf-8><title>Spec coverage</title>"
                          f"{style}{''.join(parts)}")


def main():
    global APP, OUT, QA_DIR
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    import state as kit_state
    APP = os.path.abspath(os.path.expanduser(sys.argv[1]))
    OUT = kit_state.out_dir(APP, sys.argv[2])  # never inside the app
    QA_DIR = sys.argv[sys.argv.index("--qa-reports") + 1] if "--qa-reports" in sys.argv else None
    if QA_DIR:
        QA_DIR = kit_state.out_dir(APP, QA_DIR, quiet=True)
    os.makedirs(OUT, exist_ok=True)
    specs = read_specs()
    tests, unpaired = read_tests(specs)
    problems = link(tests, specs)
    rows = build(specs, tests)
    unlinked = [t for t in tests if not t["scenarios"] and not t["stories"]]
    json.dump({"rows": rows, "tests": tests, "unpaired": unpaired,
               "unparsed_specs": [k for k, v in specs.items() if v["kind"] == "unparsed"],
               "unlinked": unlinked, "problems": problems},
              open(os.path.join(OUT, "coverage.json"), "w"), indent=2)
    write_html(rows, unlinked, unpaired, os.path.join(OUT, "coverage.html"))

    unparsed = [k for k, v in specs.items() if v["kind"] == "unparsed"]
    kinds = {k: sum(r["kind"] == k for r in rows) for k in ("ids", "numbered", "stories-only")}
    print(f"specs: {len(specs)}  rows: {len(rows)}  (scenarios with IDs: {kinds['ids']}, "
          f"numbered here: {kinds['numbered']}, story rows: {kinds['stories-only']})")
    via = {h: sum(t["paired_by"] == h for t in tests) for h in
           ("spec ran it with --name", "named in spec docs", "file/folder name")}
    print(f"tests: {len(tests)} paired {via}, {sum(len(u['tests']) for u in unpaired)} unpaired, "
          f"{len(unlinked)} paired but no story/ID")
    for s in STATUS_ORDER:
        print(f"  {s:16} {sum(r['status'] == s for r in rows)}")
    print("\nper spec (scenario-tested / story-tested / not covered):")
    for key in specs:
        rs = [r for r in rows if r["spec"] == key]
        c = [sum(r["status"] == s for r in rs) for s in ("scenario-tested", "story-tested", "not covered")]
        print(f"  {key:22} {len(rs):3}  {c[0]:3} / {c[1]:3} / {c[2]:3}")
    if unparsed:
        print("\nUNPARSED FORMAT (no scenarios or user stories found; not counted):",
              *unparsed, sep="\n  ")
    if problems:
        print("\nPROBLEMS:", *problems, sep="\n  ")
    if unpaired:
        print("\nunpaired:", *[f"{u['file']}: {u['tests'][0][:50]} ({u.get('why')})" for u in unpaired], sep="\n  ")


if __name__ == "__main__":
    main()
