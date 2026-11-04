"""Set the QA kit up for one Max Architecture app. Read-only on the app.

  setup.py <app> [--device SIM_UDID] [--android-device SERIAL] [--migrate-profile OLD.json --migrate-harness OLD.dart]

Writes <apps>/<package>/profile.json (kit/state.py: kit/apps in this repo, else
$QA_KIT_HOME/apps or ~/.qa-kit/apps):
  everything except "notes" is regenerated from the app on every run (scan.py);
  "notes" is yours: tips, data notes, aliases — kept as-is on re-run.
--migrate-* fill empty notes once from a hand-made profile / hand-written harness
(their tips, start-mode and data notes, and every old switch name as an alias).
"""
import json
import os
import re
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
KIT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import check_max  # noqa: E402
sys.path.insert(0, KIT)
import state as kit_state  # noqa: E402
import scan  # noqa: E402

PLIST_SERVICE = {  # Info.plist usage key -> name (simctl privacy service where one exists)
    "NSCameraUsageDescription": "camera", "NSMicrophoneUsageDescription": "microphone",
    "NSPhotoLibraryUsageDescription": "photos", "NSPhotoLibraryAddUsageDescription": "photos",
    "NSLocationWhenInUseUsageDescription": "location", "NSLocationAlwaysUsageDescription": "location",
    "NSLocationAlwaysAndWhenInUseUsageDescription": "location",
    "NSContactsUsageDescription": "contacts", "NSCalendarsUsageDescription": "calendar",
    "NSRemindersUsageDescription": "reminders", "NSMotionUsageDescription": "motion",
    "NSAppleMusicUsageDescription": "media-library", "NSFaceIDUsageDescription": "faceid",
    "NSSpeechRecognitionUsageDescription": "speech", "NSBluetoothAlwaysUsageDescription": "bluetooth",
}
DART_PERMISSION = {  # permission_handler Permission.<x> -> name
    "camera": "camera", "microphone": "microphone", "photos": "photos", "photosAddOnly": "photos",
    "location": "location", "locationWhenInUse": "location", "locationAlways": "location",
    "contacts": "contacts", "calendar": "calendar", "calendarFullAccess": "calendar",
    "reminders": "reminders", "sensors": "motion", "mediaLibrary": "media-library",
    "speech": "speech", "bluetooth": "bluetooth", "notification": "notifications",
}
MACRO = {"camera": "PERMISSION_CAMERA", "microphone": "PERMISSION_MICROPHONE",
         "photos": "PERMISSION_PHOTOS", "location": "PERMISSION_LOCATION",
         "contacts": "PERMISSION_CONTACTS", "calendar": "PERMISSION_EVENTS",
         "reminders": "PERMISSION_REMINDERS", "motion": "PERMISSION_SENSORS",
         "media-library": "PERMISSION_MEDIA_LIBRARY", "speech": "PERMISSION_SPEECH_RECOGNIZER",
         "bluetooth": "PERMISSION_BLUETOOTH", "notifications": "PERMISSION_NOTIFICATIONS"}
PLIST_KEY = {"camera": "NSCameraUsageDescription", "microphone": "NSMicrophoneUsageDescription",
             "photos": "NSPhotoLibraryUsageDescription", "location": "NSLocationWhenInUseUsageDescription",
             "contacts": "NSContactsUsageDescription", "calendar": "NSCalendarsUsageDescription",
             "reminders": "NSRemindersUsageDescription", "motion": "NSMotionUsageDescription",
             "media-library": "NSAppleMusicUsageDescription",
             "speech": "NSSpeechRecognitionUsageDescription",
             "bluetooth": "NSBluetoothAlwaysUsageDescription"}
PACKAGE_USES = {"camera": ["camera"], "image_picker": ["photos", "camera"],
                "geolocator": ["location"], "location": ["location"], "record": ["microphone"],
                "local_auth": ["faceid"]}
SIMCTL = {"camera", "microphone", "photos", "location", "contacts", "calendar", "reminders",
          "motion", "media-library"}


def read(path):
    return open(path, errors="replace").read() if os.path.isfile(path) else ""


def tilde(path):
    home = os.path.expanduser("~")
    return "~" + path[len(home):] if path.startswith(home + os.sep) else path


def bundle_ids(app):
    text = read(os.path.join(app, "ios", "Runner.xcodeproj", "project.pbxproj"))
    ids = sorted(set(re.findall(r"PRODUCT_BUNDLE_IDENTIFIER = \"?([\w.\-$()]+)\"?;", text)))
    return [i for i in ids if not re.search(r"Tests$", i)]


def ios_permissions(app):
    plist = read(os.path.join(app, "ios", "Runner", "Info.plist"))
    declared = sorted({PLIST_SERVICE.get(k, k) for k in re.findall(r"<key>(NS\w+UsageDescription)</key>", plist)})
    podfile = read(os.path.join(app, "ios", "Podfile"))
    macros = sorted(set(re.findall(r"'?(PERMISSION_\w+)=1'?", podfile)))
    pubspec = read(os.path.join(app, "pubspec.yaml"))
    deps = set(re.findall(r"^  (\w+):", pubspec, re.M))
    used, where = set(), {}
    for d, _, fs in os.walk(os.path.join(app, "lib")):
        for f in fs:
            if not f.endswith(".dart"):
                continue
            p = os.path.join(d, f)
            for n, line in enumerate(read(p).splitlines(), 1):
                for m in re.finditer(r"\bPermission\.(\w+)\b", line):
                    name = DART_PERMISSION.get(m.group(1))
                    if name:
                        used.add(name)
                        where.setdefault(name, f"{os.path.relpath(p, app)}:{n}")
    # A package counts only when the app's own code imports it: files that also exist at
    # the same path in the SG CLI boilerplate (e.g. its unused image picker) don't count.
    boiler = os.path.join(check_max.BOILER, "lib") if check_max.BOILER else None
    for d, _, fs in os.walk(os.path.join(app, "lib")):
        for f in fs:
            p = os.path.join(d, f)
            rel = os.path.relpath(p, os.path.join(app, "lib"))
            if not f.endswith(".dart") or (boiler and os.path.exists(os.path.join(boiler, rel))):
                continue
            for n, line in enumerate(read(p).splitlines(), 1):
                m = re.match(r"\s*import 'package:(\w+)/", line)
                if m and m.group(1) in PACKAGE_USES:
                    for name in PACKAGE_USES[m.group(1)]:
                        used.add(name)
                        where.setdefault(name, f"{os.path.relpath(p, app)}:{n} imports {m.group(1)}")
    problems = []
    for name in sorted(used):
        if name in PLIST_KEY and name not in declared:
            problems.append(f"{name}: used ({where[name]}) but ios/Runner/Info.plist has no "
                            f"{PLIST_KEY[name]} (iOS requires it before an app asks for {name})")
        if "permission_handler" in deps and name in MACRO and MACRO[name] not in macros \
                and where[name].endswith(tuple("0123456789")):
            problems.append(f"{name}: permission_handler is built without {MACRO[name]}=1 in "
                            f"ios/Podfile, so Permission.{name}.request() always returns "
                            f"permanentlyDenied on iOS (no prompt)")
    grant = sorted(n for n in used if n in SIMCTL and n in declared
                   and ("permission_handler" not in deps or MACRO.get(n) in macros
                        or not where[n].endswith(tuple("0123456789"))))
    return {"used_in_code": {n: where[n] for n in sorted(used)}, "info_plist": declared,
            "podfile_macros": macros, "simctl_grant": grant, "problems": problems}


STORES = ("drift", "sqflite", "isar", "hive", "hive_flutter", "objectbox", "realm", "sembast",
          "floor", "shared_preferences")


def persistent_stores(app):
    """Storage packages besides the boilerplate's LocalStorage: data kept in them is not
    cleared by the harness's fresh start (only LocalStorage and fixtures are)."""
    pubspec = read(os.path.join(app, "pubspec.yaml"))
    boiler = read(os.path.join(check_max.BOILER, "pubspec.yaml")) if check_max.BOILER else ""
    deps = set(re.findall(r"^  (\w+):", pubspec, re.M)) - set(re.findall(r"^  (\w+):", boiler, re.M))
    return sorted(d for d in deps if d in STORES)


# permission_handler name -> Android permissions; any one declared counts as declared
ANDROID_PERMISSION = {
    "camera": ["android.permission.CAMERA"], "microphone": ["android.permission.RECORD_AUDIO"],
    "speech": ["android.permission.RECORD_AUDIO"],
    "photos": ["android.permission.READ_MEDIA_IMAGES", "android.permission.READ_EXTERNAL_STORAGE"],
    "location": ["android.permission.ACCESS_FINE_LOCATION", "android.permission.ACCESS_COARSE_LOCATION"],
    "contacts": ["android.permission.READ_CONTACTS"],
    "calendar": ["android.permission.READ_CALENDAR", "android.permission.WRITE_CALENDAR"],
    "notifications": ["android.permission.POST_NOTIFICATIONS"],
    "bluetooth": ["android.permission.BLUETOOTH_CONNECT", "android.permission.BLUETOOTH_SCAN"],
    "motion": ["android.permission.BODY_SENSORS", "android.permission.ACTIVITY_RECOGNITION"],
}
ANDROID_RUNTIME = {p for v in ANDROID_PERMISSION.values() for p in v}  # grantable with pm grant


def android_ids(app):
    """applicationId(s) from android/app/build.gradle(.kts); flavors are reported, not resolved."""
    text = read(os.path.join(app, "android", "app", "build.gradle.kts")) or \
        read(os.path.join(app, "android", "app", "build.gradle"))
    ids = list(dict.fromkeys(re.findall(r"applicationId\s*=?\s*[\"']([\w.]+)[\"']", text)))
    flavors = bool(re.search(r"productFlavors", text))
    return ids, flavors


def plugin_manifests(app):
    """{package: AndroidManifest.xml} for every package the app resolves (needs `flutter pub get`)."""
    cfg_path = os.path.join(app, ".dart_tool", "package_config.json")
    if not os.path.isfile(cfg_path):
        return None
    found = {}
    for p in json.load(open(cfg_path)).get("packages", []):
        root = p.get("rootUri", "")
        root = root[7:] if root.startswith("file://") else \
            os.path.normpath(os.path.join(app, ".dart_tool", root))
        f = os.path.join(root, "android", "src", "main", "AndroidManifest.xml")
        if os.path.isfile(f) and p.get("name") != os.path.basename(app):
            found[p["name"]] = f
    return found


def android_permissions(app, ios):
    """Used (permission_handler calls in the app's code) vs declared in the merged manifest
    (the app's own manifests + its plugins' manifests, as Gradle merges them)."""
    if not os.path.isdir(os.path.join(app, "android")):
        return None
    declared = {}
    for flavor_dir in ("main", "debug", "profile"):
        f = os.path.join(app, "android", "app", "src", flavor_dir, "AndroidManifest.xml")
        for perm in re.findall(r"uses-permission[^>]*android:name=\"([\w.]+)\"", read(f)):
            declared.setdefault(perm, f"app ({flavor_dir})")
    plugins = plugin_manifests(app)
    for name, f in (plugins or {}).items():
        for perm in re.findall(r"uses-permission[^>]*android:name=\"([\w.]+)\"", read(f)):
            declared.setdefault(perm, f"plugin {name}")
    problems, grant = [], []
    for name, where in ios["used_in_code"].items():
        if not where.endswith(tuple("0123456789")) or name not in ANDROID_PERMISSION:
            continue  # only a Permission.x call needs a manifest entry; plugin imports bring their own
        have = [p for p in ANDROID_PERMISSION[name] if p in declared]
        if have:
            grant += [p for p in have if p in ANDROID_RUNTIME]
        elif plugins is not None:
            problems.append(f"{name}: Permission.{name} is requested ({where}) but no "
                            f"{' / '.join(ANDROID_PERMISSION[name])} is declared in the app's or its "
                            f"plugins' AndroidManifest.xml, so Android denies it with no prompt")
    return {"declared": {p: declared[p] for p in sorted(declared)},
            "plugin_manifests_read": plugins is not None,
            "pm_grant": sorted(set(grant)), "problems": problems}


def gradle_version(app):
    props = read(os.path.join(app, "android", "gradle", "wrapper", "gradle-wrapper.properties"))
    m = re.search(r"gradle-(\d+(?:\.\d+)*)-(?:all|bin)\.zip", props)
    return m.group(1) if m else None


def android_build_problems(app):
    """Plugins whose Android build cannot work with the app's Gradle: jcenter() was removed
    in Gradle 9, so a plugin that still calls it fails to configure (the whole build fails)."""
    ver = gradle_version(app)
    major = int(ver.split(".")[0]) if ver else 0
    problems = []
    if major < 9:
        return ver, problems
    pubspec = read(os.path.join(app, "pubspec.yaml"))
    direct = set(re.findall(r"^  (\w+):", pubspec, re.M))
    for name, manifest in sorted((plugin_manifests(app) or {}).items()):
        root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(manifest))))
        for g in ("build.gradle", "build.gradle.kts"):
            f = os.path.join(root, "android", g)
            if re.search(r"^\s*jcenter\(\)", read(f), re.M):
                how = "a direct dependency in pubspec.yaml" if name in direct else "pulled in by another package"
                problems.append(f"plugin {name} ({how}) still uses jcenter() in android/{g}, which "
                                f"Gradle {ver} no longer has, so the Android build fails ('Could not "
                                f"find method jcenter()'); upgrade or replace {name}")
    return ver, problems


def adb_devices():
    out = subprocess.run(["adb", "devices", "-l"], capture_output=True, text=True).stdout \
        if shutil.which("adb") else ""
    devs = []
    for line in out.splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 2:
            info = dict(p.split(":", 1) for p in parts[2:] if ":" in p)
            devs.append({"serial": parts[0], "state": parts[1], "model": info.get("model", "")})
    return devs


def adb_prop(serial, prop):
    return subprocess.run(["adb", "-s", serial, "shell", "getprop", prop],
                          capture_output=True, text=True, timeout=10).stdout.strip()


def pick_android_device(explicit=None, previous=None):
    """--android-device, else the profile's earlier choice if still ready, else the first
    ready adb device (phone or emulator) — so a second device plugged in later does not
    silently take over."""
    devs = adb_devices()
    ready = [d for d in devs if d["state"] == "device"]
    for how, pool in (("given", [d for d in ready if d["serial"] == explicit] if explicit else []),
                      ("kept from earlier setup", [d for d in ready if d["serial"] == previous] if previous else []),
                      ("connected", ready)):
        if pool:
            d = pool[0]
            return {"serial": d["serial"], "name": d["model"].replace("_", " ") or d["serial"],
                    "android": adb_prop(d["serial"], "ro.build.version.release"),
                    "api": adb_prop(d["serial"], "ro.build.version.sdk"),
                    "emulator": d["serial"].startswith("emulator-"), "chosen_as": how}
    if any(d["state"] == "unauthorized" for d in devs):
        print("  android: a device is connected but not authorized — accept the USB debugging prompt on it")
    return None


def pick_device(explicit=None):
    """--device, else the booted iPhone simulator, else the first available iPhone."""
    if not shutil.which("xcrun"):
        return None
    out = subprocess.run(["xcrun", "simctl", "list", "devices", "available", "-j"],
                         capture_output=True, text=True).stdout
    devs = [dict(x, runtime=r.split(".")[-1]) for r, v in json.loads(out or "{}").get("devices", {}).items()
            for x in v if x.get("isAvailable", True)]
    iphones = [d for d in devs if d["name"].startswith("iPhone")]
    for how, pool in (("given", [d for d in devs if d["udid"] == explicit] if explicit else []),
                      ("booted iPhone", [d for d in iphones if d.get("state") == "Booted"]),
                      ("first available iPhone", sorted(iphones, key=lambda d: [int(x) for x in re.findall(r"\d+", d["runtime"])], reverse=True))):
        if pool:
            d = pool[0]
            return {"udid": d["udid"], "name": d["name"], "runtime": d["runtime"], "chosen_as": how}
    return None


def start_modes(found):
    modes = {
        "fresh": {"kind": "builtin", "does": "first launch: storage cleared, then initializeApp() + MyApp()"},
        "relaunch": {"kind": "builtin", "does": "restart keeping saved storage; fake data reset"},
    }
    helpers = {h["name"]: h for h in found["helpers"]}
    for p in found["pairs"]:
        name = re.sub(r"(?<!^)(?=[A-Z])", "_", p["name"]).lower()
        seed, drive = helpers[p["seed"]], helpers[p["drive"]]
        modes[name] = {"kind": "helpers", "seed": p["seed"], "drive": p["drive"], "file": seed["file"],
                       "drive_file": drive["file"],
                       "options": {a["name"]: {"type": a["type"], "default": a["default"]}
                                   for a in seed["args"] if a["kind"] != "positional"}}
    for r in found["routes"]:
        if r["direct_start"]:
            modes[f"route:{r['name']}"] = {
                "kind": "route", "path": r["path"],
                "does": f"MyApp(initialRoute: '{r['path']}') after a fresh start"
                        + (f"; opens with no {', '.join(r['optional_args'])} (optional, not passed)"
                           if r["optional_args"] else "")}
    return modes


def migrate_harness_aliases(dart_path, switches):
    """Old harness `case 'x': FakeY.field = ...` -> alias x -> generated switch name."""
    text = read(dart_path)
    by_field = {(s["class"], s["field"]): s["name"] for s in switches}
    aliases, unresolved = {}, []
    for m in re.finditer(r"case '(\w+)':\s*(?://[^\n]*\s*)*(\w+)\.(\w+)\s*=", text):
        alias, cls, field = m.groups()
        target = by_field.get((cls, field))
        if target:
            aliases[alias] = target
        else:
            unresolved.append(f"{alias} -> {cls}.{field}")
    return aliases, unresolved


def migrate_profile_notes(old):
    return {
        "tips": list(old.get("tips", [])),
        "test_data_notes": dict(old.get("test_data", {})),
        "start_mode_notes": dict(old.get("start_modes", {})),
        "switch_notes": old.get("variants_note", ""),
    }


def main(argv):
    if not argv or argv[0].startswith("-"):
        raise SystemExit(__doc__)
    app = os.path.abspath(os.path.expanduser(argv[0]))
    opt = {argv[i]: argv[i + 1] for i in range(1, len(argv) - 1) if argv[i].startswith("--")}
    if not check_max.check_app(app):
        raise SystemExit("setup stopped: not a usable Max app (see above)")
    found = scan.scan(app)
    pkg = found["package"]
    out_dir = os.path.join(kit_state.apps_dir(), pkg)
    out = os.path.join(out_dir, "profile.json")
    old = json.load(open(out)) if os.path.exists(out) else {}
    notes = old.get("notes") or {"tips": [], "test_data_notes": {}, "start_mode_notes": {},
                                 "switch_notes": "", "aliases": {"start_modes": {}, "switches": {}}}
    notes.setdefault("aliases", {"start_modes": {}, "switches": {}})
    if "--migrate-profile" in opt:
        hand = json.load(open(os.path.expanduser(opt["--migrate-profile"])))
        for k, v in migrate_profile_notes(hand).items():
            if not notes.get(k):
                notes[k] = v
        if "home_owner" in hand.get("start_modes", {}) and "home" in start_modes(found):
            notes["aliases"]["start_modes"].setdefault(
                "home_owner", {"mode": "home", "options": {"owner": True}})
    if "--migrate-harness" in opt:
        aliases, unresolved = migrate_harness_aliases(os.path.expanduser(opt["--migrate-harness"]),
                                                      found["switches"])
        for a, t in aliases.items():
            notes["aliases"]["switches"].setdefault(a, t)
        for u in unresolved:
            print(f"  alias not migrated (no such switch now): {u}")
    modes = start_modes(found)
    names = {s["name"] for s in found["switches"]}
    bad = [f"switch alias {a} -> {t}" for a, t in notes["aliases"]["switches"].items() if t not in names]
    bad += [f"start alias {a} -> {t['mode']}" for a, t in notes["aliases"]["start_modes"].items()
            if t["mode"] not in modes]
    device = pick_device(opt.get("--device"))
    android_device = pick_android_device(opt.get("--android-device"),
                                         ((old.get("app") or {}).get("android_device") or {}).get("serial"))
    a_ids, a_flavors = android_ids(app)
    ios_perms = ios_permissions(app)
    profile = {
        "about": "Generated by kit/setup/setup.py from the app's code. Edit only \"notes\" — "
                 "everything else is rewritten on the next run.",
        "app": {"name": pkg, "path": tilde(app), "package": pkg, "bundle_ids": bundle_ids(app),
                "device": device, "android_ids": a_ids, "android_flavors": a_flavors,
                "android_device": android_device},
        "ios_permissions": ios_perms,
        "android_permissions": android_permissions(app, ios_perms),
        "android_build": dict(zip(("gradle", "problems"), android_build_problems(app))),
        "persistent_stores": persistent_stores(app),
        "start_modes": modes,
        "switches": found["switches"],
        "resets": found["resets"],
        "routes_not_direct": [{"name": r["name"], "path": r["path"], "why": r["why_not"]}
                              for r in found["routes"] if not r["direct_start"]],
        "default_route": found["default_route"],
        "helpers": found["helpers"],
        "helpers_ignored": found["ignored"],
        "test_data": found["test_data"],
        "skipped": found["skipped"],
        "notes": notes,
    }
    os.makedirs(out_dir, exist_ok=True)
    tmp = out + ".tmp"
    with open(tmp, "w") as f:
        json.dump(profile, f, indent=1, ensure_ascii=False)
        f.write("\n")
    os.replace(tmp, out)  # never leave a half-written profile
    perms = profile["ios_permissions"]
    print(f"wrote {tilde(out)}")
    ad = android_device
    print(f"  iOS simulator: {device['name'] + ' ' + device['runtime'] + ' (' + device['chosen_as'] + ')' if device else 'none found'}")
    print(f"  Android device: {ad['name'] + ' Android ' + ad['android'] + ' API ' + ad['api'] + ' ' + ad['serial'] + ' (' + ad['chosen_as'] + ')' if ad else 'none connected'}")
    print(f"  bundle id: {', '.join(profile['app']['bundle_ids']) or '?'}; android id: {', '.join(a_ids) or '?'}")
    if a_flavors:
        print("  NOTE: android/app/build.gradle has productFlavors; the kit runs without --flavor")
    print(f"  start modes: {', '.join(modes)}; switches {len(found['switches'])}; "
          f"callable helpers {sum(h['callable'] for h in found['helpers'])}/{len(found['helpers'])}; "
          f"test-data files {len(found['test_data'])}")
    print(f"  iOS permissions used: {', '.join(perms['used_in_code']) or 'none'}; declared: "
          f"{', '.join(perms['info_plist']) or 'none'}; simulator grants: {', '.join(perms['simctl_grant']) or 'none'}")
    if profile["persistent_stores"]:
        print(f"  own storage not reset by 'fresh': {', '.join(profile['persistent_stores'])}")
    for p in perms["problems"]:
        print(f"  APP SETUP PROBLEM (iOS): {p}")
    ap = profile["android_permissions"]
    if ap:
        print(f"  android permissions declared (merged): {', '.join(p.split('.')[-1] for p in ap['declared']) or 'none'}; "
              f"device grants: {', '.join(p.split('.')[-1] for p in ap['pm_grant']) or 'none'}")
        if not ap["plugin_manifests_read"]:
            print("  NOTE: no .dart_tool/package_config.json (run flutter pub get): plugin manifests not read")
        for p in ap["problems"]:
            print(f"  APP SETUP PROBLEM (Android): {p}")
    for p in profile["android_build"]["problems"]:
        print(f"  APP SETUP PROBLEM (Android build): {p}")
    n = notes["aliases"]
    print(f"  notes: {len(notes['tips'])} tips, {len(notes['test_data_notes'])} data notes, "
          f"aliases {len(n['start_modes'])} start / {len(n['switches'])} switch")
    for b in bad:
        print(f"  BROKEN ALIAS: {b}")
    return 1 if bad or not (device or android_device) else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
