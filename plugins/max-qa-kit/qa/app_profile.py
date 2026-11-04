"""Read an app's generated profile (kit/apps/<app>/profile.json) for /qa.

load(x) takes an app name ("sdd"), an app folder, or a profile.json path.
The views below are what the planner, the driver and the judge are shown.
"""
import json
import os
import subprocess
import sys

KIT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, KIT)
import state as kit_state  # noqa: E402

APPS = kit_state.apps_dir()
SWITCH_HINT = {"bool": "true|false", "int": "a whole number", "double": "a number",
               "string": "text", "duration": "e.g. 300ms or 2s",
               "map_bool": "key=true,key2=false", "list_string": "a,b,c", "set_string": "a,b,c"}


def resolve(x):
    x = os.path.expanduser(x)
    if x.endswith(".json") and os.path.isfile(x):
        return os.path.abspath(x)
    if os.path.isfile(os.path.join(APPS, x, "profile.json")):
        return os.path.join(APPS, x, "profile.json")
    if os.path.isdir(x):  # an app folder: its package name names the profile
        import re
        if not os.path.isfile(os.path.join(x, "pubspec.yaml")):
            raise SystemExit(f"{x} is not a Flutter app (no pubspec.yaml)")
        m = re.search(r"^name:\s*(\w+)", open(os.path.join(x, "pubspec.yaml")).read(), re.M)
        p = os.path.join(APPS, m.group(1), "profile.json") if m else ""
        if os.path.isfile(p):
            return p
        print(f"no profile for {x} yet: running setup", flush=True)  # first use of an app
        r = subprocess.run(["python3", "-I", os.path.join(KIT, "setup", "setup.py"), x])
        if r.returncode == 0 and os.path.isfile(p):
            return p
        raise SystemExit(f"setup did not produce a profile for {x} (see above)")
    raise SystemExit(f"no app profile for {x!r} (looked in {APPS})")


def load(x):
    path = resolve(x)
    p = json.load(open(path))
    if "app" not in p or "start_modes" not in p:
        raise SystemExit(f"{path} is not a generated profile (kit/setup/setup.py makes one)")
    p["_path"] = path
    return p


def refresh(p):
    """Re-read the app (setup.py keeps notes) so the profile matches today's code."""
    r = subprocess.run(["python3", "-I", os.path.join(KIT, "setup", "setup.py"), app_path(p)],
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit("setup failed:\n" + (r.stdout + r.stderr)[-1500:])
    return load(p["_path"])


def app_path(p):
    return os.path.expanduser(p["app"]["path"])


def adb_ready():
    import shutil
    if not shutil.which("adb"):
        return []
    out = subprocess.run(["adb", "devices"], capture_output=True, text=True).stdout
    return [l.split()[0] for l in out.splitlines()[1:] if len(l.split()) >= 2 and l.split()[1] == "device"]


def simulators():
    import shutil
    if not shutil.which("xcrun"):
        return set()
    out = subprocess.run(["xcrun", "simctl", "list", "devices", "available", "-j"],
                         capture_output=True, text=True).stdout
    return {d["udid"] for v in json.loads(out or "{}").get("devices", {}).values() for d in v}


def device(p, override=None, platform=None):
    """(platform, id) to run on. --device wins (a simulator udid or an adb serial); else
    --platform android -> the profile's Android device if still connected, else any connected
    one; else (default, iOS) the profile's simulator. It must be available now."""
    if override:
        if override in adb_ready():
            return "android", override
        if override in simulators():
            return "ios", override
        raise SystemExit(f"device {override} is neither a ready adb device nor an available simulator")
    if platform == "android":
        ready = adb_ready()
        want = (p["app"].get("android_device") or {}).get("serial")
        if want in ready:
            return "android", want
        if ready:
            return "android", ready[0]
        raise SystemExit("no Android device ready (adb devices): connect and unlock a phone, "
                         "allow USB debugging, or start an emulator")
    udid = (p["app"].get("device") or {}).get("udid")
    if not udid or udid not in simulators():
        raise SystemExit(f"simulator {udid} not available — re-run setup.py or pass --device")
    return "ios", udid


def platform_id(p, platform):
    """The app's id on that platform (bundle id / applicationId)."""
    ids = p["app"].get("android_ids") if platform == "android" else p["app"].get("bundle_ids")
    return (ids or [""])[0]


def grants(p, platform):
    """Permissions run.py grants up front, and the app-setup problems for that platform."""
    if platform == "android":
        a = p.get("android_permissions") or {}
        return a.get("pm_grant", []), a.get("problems", []) + (p.get("android_build") or {}).get("problems", [])
    i = p.get("ios_permissions") or {}
    return i.get("simctl_grant", []), i.get("problems", [])


def notes(p):
    return p.get("notes") or {}


def describe_mode(p, mode):
    """One line saying what a start mode sets up (hand notes first)."""
    n = notes(p).get("start_mode_notes", {})
    if mode in n:
        return n[mode]
    m = p["start_modes"].get(mode)
    alias = notes(p).get("aliases", {}).get("start_modes", {}).get(mode)
    if not m and alias:
        return f"{describe_mode(p, alias['mode'])}, with options {alias.get('options', {})}"
    if not m:
        return ""
    if m["kind"] == "helpers":
        opts = ", ".join(f"{k} ({v['type']}, default {v['default']})" for k, v in m["options"].items())
        return (f"the app's own test setup {m['seed']}() + {m['drive']}(tester)"
                + (f"; options: {opts}" if opts else ""))
    return m.get("does", "")


def start_modes_view(p):
    modes = {k: describe_mode(p, k) for k in p["start_modes"]}
    for k in notes(p).get("aliases", {}).get("start_modes", {}):
        modes.setdefault(k, describe_mode(p, k))
    return modes


def switches_view(p):
    out = {}
    for s in p["switches"]:
        out[s["name"]] = " | ".join(s["values"]) if s["kind"] == "enum" else SWITCH_HINT[s["kind"]]
        if s.get("default") is not None:
            out[s["name"]] += f"  (default {s['default']})"
    return out


def helpers_view(p):
    """Team test helpers the driver may call as one step: `helper <name> args`."""
    out = []
    for h in p["helpers"]:
        if not (h["callable"] and h["tester"]):
            continue
        args = [f"{a['type']} {a['name']}" + (f" = {a['default']}" if a["default"] else "")
                + ("" if a["kind"] == "positional" else f" [{a['kind']}]") for a in h["args"]]
        out.append(f"{h['name']}({', '.join(args)})")
    return out


def test_data_view(p):
    """Readable fake data per fixture member, one line per row."""
    out = {}
    for f in p["test_data"]:
        for m in f["members"]:
            key = f"{f['file']} {m['member']}"
            out[key] = ([" | ".join(f"{k}: {v}" for k, v in row.items()) for row in m["rows"]]
                        if "rows" in m else m["values"])
    return out


def app_view(p):
    """Everything the planner and the driver need to know about the app."""
    n = notes(p)
    return {
        "start_modes": start_modes_view(p),
        "start_note": "route:<name> opens that screen alone (nothing behind it, so Back may "
                      "leave the app); screens that need arguments can't be started directly: "
                      + ", ".join(f"{r['name']} ({r['why']})" for r in p.get("routes_not_direct", [])),
        "saved_data_note": (f"The app also saves data with {', '.join(p['persistent_stores'])}: "
                            "a start (even fresh) does NOT clear it, so data made in an earlier "
                            "scenario of the same run is still there; each run starts from a "
                            "fresh install") if p.get("persistent_stores") else "",
        "switches": switches_view(p),
        "switch_aliases": n.get("aliases", {}).get("switches", {}),
        "switch_notes": n.get("switch_notes", ""),
        "helpers": helpers_view(p),
        "test_data": test_data_view(p),
        "test_data_notes": n.get("test_data_notes", {}),
        "tips": n.get("tips", []),
    }


def driver_view(p):
    """Smaller view for the driver: it follows a goal the planner already wrote with
    real data, so it needs names to type and tap, not every field of every row."""
    v = app_view(p)
    v["test_data"] = {k: [" | ".join(r.split(" | ")[:2]) for r in rows[:15]]
                      for k, rows in v["test_data"].items()}
    v["switches"] = sorted(v["switches"])  # names only; values come from the plan
    v.pop("switch_aliases", None)
    return v

