"""Check the Max Architecture contract (kit/setup/max_contract.json).

  check_max.py --verify-boilerplate   every quote is at its boilerplate file:line
  check_max.py --write-md             regenerate kit/setup/MAX_CONTRACT.md
  check_max.py <app>                  is <app> a Max app the kit can use?

Read-only on the app. Exit code 0 = all required items present.
"""
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
CONTRACT = json.load(open(os.path.join(HERE, "max_contract.json")))


def find_boilerplate():
    """The SG CLI 'max' boilerplate in this machine's pub cache: the contract's checked path
    if present, else the newest sg_cli checkout (the hash in the folder name differs per
    machine), else "" (setup still works; boilerplate-based filters are then skipped)."""
    import glob
    want = os.path.expanduser(CONTRACT["boilerplate"])
    if os.path.isdir(want):
        return want
    found = glob.glob(os.path.expanduser(
        "~/.pub-cache/git/sg_cli-*/lib/versions/max/commands/init/boilerplate"))
    return max(found, key=os.path.getmtime) if found else ""


BOILER = find_boilerplate()


def verify_boilerplate():
    if not BOILER:
        print("  SG CLI boilerplate not found in ~/.pub-cache/git (install sg_cli first)")
        return 1
    misses = 0
    for it in CONTRACT["items"]:
        f = it.get("boilerplate_file")
        if not f:
            print(f"  -     {it['id']:24} (no boilerplate source: {it.get('sdd_source', '')[:60]})")
            continue
        path = os.path.join(BOILER, f)
        if not os.path.isfile(path):
            print(f"  MISS  {it['id']:24} file not found: {f}")
            misses += 1
            continue
        lines = open(path).read().splitlines()
        n = it["boilerplate_line"]
        line = lines[n - 1] if 0 < n <= len(lines) else ""
        ok = it["quote"] in line if it["quote"] else True
        misses += not ok
        print(f"  {'ok  ' if ok else 'MISS'}  {it['id']:24} {f}:{n}" + ("" if ok else f"  found: {line!r}"))
    print(f"{misses} misses")
    return misses == 0


def write_md():
    L = ["# Max Architecture contract for the QA kit", "",
         "## In short", "",
         "What the AI QA kit relies on in **every** Max Architecture app (from the SG CLI",
         "`sg init` boilerplate) and in our SDD specs. Everything else — fake-data switches,",
         "test helpers, test data, routes, permissions — is discovered per app by `setup`.",
         "Generated from `max_contract.json` by `check_max.py --write-md`; check any app with",
         "`python3 -I kit/setup/check_max.py <app>`.", "",
         "| Item | Need | Used for | Boilerplate source | What is checked in an app |",
         "|---|---|---|---|---|"]
    for it in CONTRACT["items"]:
        src = (f"`{it['boilerplate_file']}:{it['boilerplate_line']}`" + (f" `{it['quote']}`" if it["quote"] else "")
               if it.get("boilerplate_file") else it.get("sdd_source", ""))
        chk = (f"folder `{it['app_dir']}/`" if it.get("app_dir")
               else f"`{it['app_file']}` matches `{it['pattern']}`")
        L.append(f"| {it['id']} | {it['need']} | {it['used_for']} | {src} | {chk} |")
    L += ["", "## Discovered per app (not part of the contract)", "",
          "- fake-data switches in `lib/core/fake_models/` (enum fields, bool maps, `reset()`)",
          "- start helpers in `integration_test/**` (`seedX()`, `driveToX(tester)`; Patrol-only ones are listed, not called)",
          "- route names in `_route_names.dart` (top-level `/x` routes vs nested `parent/child` routes)",
          "- test data per fixture file; iOS permissions from `ios/Runner/Info.plist`; bundle id; simulator"]
    path = os.path.join(HERE, "MAX_CONTRACT.md")
    open(path, "w").write("\n".join(L) + "\n")
    print(f"wrote {path}")


def check_app(app):
    app = os.path.abspath(os.path.expanduser(app))
    print(f"app: {app}")
    missing_required, facts = [], {}
    for it in CONTRACT["items"]:
        if it.get("app_dir"):
            ok = os.path.isdir(os.path.join(app, it["app_dir"]))
        else:
            path = os.path.join(app, it["app_file"])
            text = open(path, errors="replace").read() if os.path.isfile(path) else ""
            m = re.search(it["pattern"], text, re.M)
            ok = bool(m)
            if it["id"] == "package_name" and m:
                facts["package"] = m.group(1)
        if not ok and it["need"] == "required":
            missing_required.append(it["id"])
        print(f"  {'ok  ' if ok else ('MISS' if it['need'] == 'required' else ' -  ')}  "
              f"{it['id']:24} {it['need']:8} {it['used_for']}")

    def count(cmd):
        try:
            return subprocess.run(cmd, cwd=app, capture_output=True, text=True, shell=True).stdout.strip()
        except Exception:  # noqa: BLE001
            return "?"
    rn = os.path.join(app, "lib/app/app_routes/_route_names.dart")
    routes = re.findall(r"static const String (\w+) = '([^']*)'", open(rn).read()) if os.path.isfile(rn) else []
    fm = os.path.join(app, "lib/core/fake_models")
    specs = os.path.join(app, "specs")
    facts.update({
        "routes": f"{len(routes)} ({sum(r.startswith('/') for _, r in routes)} top-level, "
                  f"{sum(not r.startswith('/') for _, r in routes)} nested)",
        "fake_models files": len([f for f in os.listdir(fm) if f.endswith(".dart")]) if os.path.isdir(fm) else 0,
        "integration tests": count("find integration_test -name '*_test.dart' 2>/dev/null | wc -l"),
        "spec folders": len([d for d in os.listdir(specs) if os.path.isfile(os.path.join(specs, d, "spec.md"))])
        if os.path.isdir(specs) else 0,
        "uncommitted files": count("git status --porcelain 2>/dev/null | wc -l"),
    })
    for k, v in facts.items():
        print(f"  {k}: {v}")
    if missing_required:
        print(f"NOT a usable Max app — missing required: {', '.join(missing_required)}")
        return False
    print("Max app: yes — the kit can set itself up here")
    return True


if __name__ == "__main__":
    a = sys.argv[1:]
    if a == ["--verify-boilerplate"]:
        sys.exit(0 if verify_boilerplate() else 1)
    if a == ["--write-md"]:
        write_md()
    elif len(a) == 1:
        sys.exit(0 if check_app(a[0]) else 1)
    else:
        raise SystemExit(__doc__)
