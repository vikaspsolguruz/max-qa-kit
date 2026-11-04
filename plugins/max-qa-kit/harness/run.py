"""Run one live QA session on the iOS simulator or an Android device (phone or emulator).

run.py is the FOREGROUND parent (no background processes):
  1. lock the simulator, copy the harness into the app's integration_test/
  2. start a local server, start `flutter test` with the harness
  3. wait until the app asks for its first step
  4. run the DRIVER command as a child (a script, or `claude -p ...`);
     the driver sends steps with step.py and sees each screen
  5. always: tell the harness to finish, stop processes, remove the harness,
     put every file back byte for byte, release the lock

Files: before anything runs, every modified / deleted / untracked file of the app
(git status -uall) is copied to <out>/app_backup/before/ (manifest.json lists
them with sha256). Afterwards each one that changed is put back exactly; the
changed version is kept in <out>/app_backup/after/ so a parallel edit is never
lost. Tracked files that were clean are restored with git checkout (their
changed version also kept in after/). Files the session created (build
leftovers like ios/Podfile.lock) are removed, a copy kept in after/. After a hard kill (SIGKILL) the next run refuses until
`run.py --restore-from <out>` has put the files back.

Android: the device must be ready in `adb devices`, awake and unlocked. The harness reaches
this Mac through `adb reverse` (removed afterwards). If the app is already installed the
run is refused unless --replace-installed (flutter test may uninstall it and lose its data);
an app that was not installed before is uninstalled afterwards. Grants use `pm grant`
once the app is installed.

Usage:
  python3 -I run.py --app <flutter app> --device <sim udid | adb serial> --out <dir>
                    --driver "<command>" --harness <file> [--platform ios|android]
                    [--app-id <bundle id / applicationId>] [--grant camera,microphone]
                    [--replace-installed] [--build-timeout 540] [--flutter "fvm flutter"]
  python3 -I run.py --restore-from <out>
The driver gets QA_SERVER, QA_OUT and QA_PLATFORM in its environment.
"""
import argparse
import hashlib
import http.server
import json
import os
import queue
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
HARNESS_NAME = "zz_qa_harness_test.dart"
sys.path.insert(0, os.path.dirname(HERE))
import tools as kit_tools  # noqa: E402
import state as _kit_state  # noqa: E402


def kit_state_out(app, out):
    return _kit_state.out_dir(os.path.abspath(os.path.expanduser(app)), out)


class Session:
    def __init__(self, out, device, platform="ios"):
        self.out, self.device, self.platform = out, device, platform
        self.steps = queue.Queue()
        self.results = queue.Queue()
        self.connected = threading.Event()
        self.dead = threading.Event()  # set when `flutter test` has exited
        self.n = 0


def make_handler(sess):
    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, obj, code=200):
            body = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _body(self):
            if self.headers.get("Content-Length"):
                return json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            data = b""  # chunked transfer encoding
            while True:
                size = int(self.rfile.readline().strip() or b"0", 16)
                if size == 0:
                    self.rfile.readline()
                    return json.loads(data)
                data += self.rfile.read(size)
                self.rfile.readline()

        def handle_one_request(self):
            try:
                super().handle_one_request()
            except Exception as e:  # noqa: BLE001 - log, never fail silently
                with open(os.path.join(sess.out, "server_errors.log"), "a") as f:
                    f.write(f"{time.strftime('%H:%M:%S')} {self.path if hasattr(self, 'path') else ''}: {e!r}\n")
                raise

        def do_GET(self):
            if self.path == "/next":  # harness long-poll
                sess.connected.set()
                try:
                    self._send(sess.steps.get(timeout=20))
                except queue.Empty:
                    self._send({"action": "noop"})
            elif self.path == "/status":
                self._send({"ready": sess.connected.is_set()})
            else:
                self._send({"error": "unknown path"}, 404)

        def do_POST(self):
            if self.path == "/result":  # from the harness
                r = self._body()
                n = r.get("n", 0)
                if r.get("step", {}).get("action") != "finish":
                    png = os.path.join(sess.out, f"step_{n:03d}.png")
                    r["screenshot"] = png if screenshot(sess, png) else None
                    with open(os.path.join(sess.out, f"step_{n:03d}.json"), "w") as f:
                        json.dump(r, f, indent=2)
                self._send({"ok": True})
                sess.results.put(r)
            elif self.path == "/step":  # from step.py
                step = self._body()
                if sess.dead.is_set():
                    return self._send({"status": "failed", "error": "the app test has stopped"})
                sess.steps.put(step)
                deadline = time.time() + step.get("wait_s", 120)
                while time.time() < deadline:
                    try:
                        return self._send(sess.results.get(timeout=1))
                    except queue.Empty:
                        if sess.dead.is_set():
                            return self._send({"status": "failed",
                                               "error": "the app test stopped; see flutter_test.log"})
                self._send({"status": "failed",
                            "error": "no answer from the app within the wait time"})
            else:
                self._send({"error": "unknown path"}, 404)
    return H


def screenshot(sess, png):
    if sess.platform == "android":
        shot = subprocess.run([kit_tools.adb(), "-s", sess.device, "exec-out", "screencap", "-p"],
                              capture_output=True, timeout=30)
        ok = shot.returncode == 0 and shot.stdout.startswith(b"\x89PNG")
        if ok:
            open(png, "wb").write(shot.stdout)
        return ok
    shot = subprocess.run(["xcrun", "simctl", "io", sess.device, "screenshot", "--type=png", png],
                          capture_output=True, text=True)
    return shot.returncode == 0


def adb(serial, *a, timeout=60):
    return subprocess.run([kit_tools.adb(), "-s", serial, *a], capture_output=True, text=True, timeout=timeout)


def android_ready(serial, app_id, replace_installed):
    """Refuse with a reason unless the device can run a session now. Returns was_installed."""
    state = adb(serial, "get-state").stdout.strip()
    if state != "device":
        raise RuntimeError(f"Android device {serial} is '{state or 'not connected'}' — connect it, "
                           f"allow USB debugging, and check `adb devices`")
    power = adb(serial, "shell", "dumpsys", "power").stdout
    if "mWakefulness=Awake" not in power:
        adb(serial, "shell", "input", "keyevent", "KEYCODE_WAKEUP")
        time.sleep(1)
    win = adb(serial, "shell", "dumpsys", "window").stdout
    if "isKeyguardShowing=true" in win or "mDreamingLockscreen=true" in win:
        raise RuntimeError(f"the Android device {serial} is locked — unlock it and run again")
    installed = bool(app_id) and adb(serial, "shell", "pm", "path", app_id).stdout.startswith("package:")
    if installed and not replace_installed:
        raise RuntimeError(f"{app_id} is already installed on {serial}; flutter test would replace it "
                           f"and may wipe its data. Uninstall it, or pass --replace-installed to accept that")
    return installed


ANDROID_FAILURES = {
    "INSTALL_FAILED_USER_RESTRICTED": "the phone refused the install over USB — turn on 'Install via "
                                      "USB' (and 'USB debugging (Security settings)' on some brands) "
                                      "in Developer options, or accept the prompt on the phone",
    "INSTALL_FAILED_UPDATE_INCOMPATIBLE": "a build of the app signed with another key is installed — "
                                          "uninstall it first",
    "INSTALL_FAILED_INSUFFICIENT_STORAGE": "the phone is out of storage",
    "Gradle threw an error while downloading": "Gradle could not download its build tools (a first "
                                               "build of this Gradle version needs the network) — run "
                                               "`qa-kit get gradle <app>` (resumable, sha256-checked), then run again",
    "Could not find method jcenter()": "a plugin still uses jcenter(), which this Gradle version "
                                       "removed: the app does not build for Android (an app problem; "
                                       "setup lists the plugin)",
    "device offline": "adb lost the device — reconnect the cable",
    "unauthorized": "USB debugging is not authorized — accept the prompt on the phone",
}


def android_cleanup(state):
    """Undo what a session did on the device: the port reverse, and the app if it was not there."""
    done = {}
    serial = state.get("device")
    if state.get("reverse"):
        done["reverse_removed"] = adb(serial, "reverse", "--remove", state["reverse"]).returncode == 0
    if state.get("app_id") and state.get("was_installed") is False:
        r = adb(serial, "uninstall", state["app_id"], timeout=120)
        done["uninstalled"] = "Success" in r.stdout or "not installed" in (r.stdout + r.stderr) \
            or "DELETE_FAILED_INTERNAL_ERROR" in r.stdout
    return done


def get_theme(platform, device):
    """'light' / 'dark' (Android may also say 'auto'; None if unknown)."""
    try:
        if platform == "android":
            out = adb(device, "shell", "cmd", "uimode", "night").stdout  # "Night mode: yes|no|auto"
            v = out.strip().rsplit(" ", 1)[-1] if "Night mode" in out else ""
            return {"yes": "dark", "no": "light", "auto": "auto"}.get(v)
        out = subprocess.run(["xcrun", "simctl", "ui", device, "appearance"],
                             capture_output=True, text=True, timeout=20).stdout.strip()
        return out if out in ("light", "dark") else None
    except Exception:  # noqa: BLE001 - recording the theme must never stop a run
        return None


def set_theme(platform, device, theme):
    if platform == "android":
        val = {"dark": "yes", "light": "no", "auto": "auto"}[theme]
        return adb(device, "shell", "cmd", "uimode", "night", val).returncode == 0
    return subprocess.run(["xcrun", "simctl", "ui", device, "appearance", theme],
                          capture_output=True, timeout=20).returncode == 0


def restore_theme(state):
    """Put the device's light/dark setting back if the session changed it."""
    if state.get("theme_set") and state.get("theme_before") and not state.get("theme_restored"):
        ok = set_theme(state["platform"], state["device"], state["theme_before"])
        state["theme_restored"] = ok
        return ok
    return None


def git(app, *a):
    return subprocess.run(["git", "-C", app, *a], capture_output=True, text=True).stdout


def dirty_files(app):
    return {l[3:] for l in git(app, "status", "--porcelain").splitlines()}


def checksum(app):
    return hashlib.md5((git(app, "status", "--porcelain") +
                        git(app, "diff")).encode()).hexdigest()


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def status_paths(app):
    """Every path git reports as not clean, untracked folders expanded (-uall)."""
    out = subprocess.run(["git", "-C", app, "status", "--porcelain=v1", "-z", "-uall"],
                         capture_output=True).stdout.decode("utf-8", "surrogateescape")
    items, paths = out.split("\0"), []
    i = 0
    while i < len(items):
        e = items[i]
        if len(e) > 3:
            paths.append(e[3:])
            if e[0] in "RC":  # rename/copy: the next item is the old path
                paths.append(items[i + 1])
                i += 1
        i += 1
    return sorted(set(paths))


def pointer_file(app):
    return f"/tmp/qa-harness-backup-{hashlib.md5(app.encode()).hexdigest()[:12]}.json"


def take_backup(app, out):
    """Copy every not-clean file byte for byte before the session touches the app."""
    root = os.path.join(out, "app_backup")
    if os.path.exists(root):
        shutil.rmtree(root)
    entries = []
    for rel in status_paths(app):
        src = os.path.join(app, rel)
        if os.path.islink(src) or os.path.isdir(src):
            entries.append({"path": rel, "state": "skipped", "why": "link or folder"})
            continue
        if os.path.isfile(src):
            dst = os.path.join(root, "before", rel)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(src, dst)
            digest = sha256(src)
            if sha256(dst) != digest:
                raise RuntimeError(f"backup copy of {rel} does not match; not starting")
            entries.append({"path": rel, "state": "file", "sha256": digest})
        else:
            entries.append({"path": rel, "state": "absent"})
    manifest = {"app": app, "root": root, "taken": time.strftime("%Y-%m-%d %H:%M:%S"),
                "restored": False, "entries": entries}
    os.makedirs(root, exist_ok=True)
    json.dump(manifest, open(os.path.join(root, "manifest.json"), "w"), indent=1)
    json.dump({"manifest": os.path.join(root, "manifest.json")}, open(pointer_file(app), "w"))
    return manifest


def keep_after(root, app, rel):
    src = os.path.join(app, rel)
    if os.path.isfile(src):
        dst = os.path.join(root, "after", rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(src, dst)


def restore_backup(manifest):
    """Put every backed-up file back exactly. Returns (restored, failed)."""
    app, root = manifest["app"], manifest["root"]
    restored, failed = [], []
    for e in manifest["entries"]:
        rel, path = e["path"], os.path.join(manifest["app"], e["path"])
        try:
            if e["state"] == "file":
                if os.path.isfile(path) and sha256(path) == e["sha256"]:
                    continue
                keep_after(root, app, rel)
                os.makedirs(os.path.dirname(path), exist_ok=True)
                shutil.copy2(os.path.join(root, "before", rel), path)
                if sha256(path) != e["sha256"]:
                    raise RuntimeError("sha256 differs after restore")
                restored.append(rel)
            elif e["state"] == "absent" and os.path.lexists(path):
                keep_after(root, app, rel)
                os.remove(path)
                restored.append(rel)
        except Exception as ex:  # noqa: BLE001
            failed.append(f"{rel}: {ex}")
    manifest["restored"] = not failed
    manifest["restored_files"], manifest["restore_failed"] = restored, failed
    json.dump(manifest, open(os.path.join(root, "manifest.json"), "w"), indent=1)
    if not failed and os.path.exists(pointer_file(app)):
        os.remove(pointer_file(app))
    return restored, failed


def restore_from(out):
    mf = os.path.join(os.path.abspath(out), "app_backup", "manifest.json")
    manifest = json.load(open(mf))
    harness = os.path.join(manifest["app"], "integration_test", HARNESS_NAME)
    if os.path.isfile(harness) and "QA harness" in open(harness, errors="replace").read(400):
        os.remove(harness)  # left behind by the killed run
        print(f"removed leftover {harness}")
    restored, failed = restore_backup(manifest)
    dev_state = os.path.join(os.path.abspath(out), "device_state.json")
    if os.path.isfile(dev_state):
        st = json.load(open(dev_state))
        if st.get("platform") == "android" and not st.get("cleaned"):
            print(f"android device: {android_cleanup(st)}")
        if restore_theme(st) is not None:
            print(f"device theme put back to {st['theme_before']}")
    app = manifest["app"]
    known = {e["path"] for e in manifest["entries"]}
    tracked = [f for f in status_paths(app) if f not in known and git(app, "ls-files", f).strip()]
    for f in tracked:  # clean before the run: back to the committed version (copy kept in after/)
        keep_after(manifest["root"], app, f)
    if tracked:
        git(app, "checkout", "--", *tracked)
    restored += tracked
    print(f"restored {len(restored)} file(s) into {app}: {restored}")
    for f in failed:
        print(f"RESTORE FAILED: {f}")
    return 1 if failed else 0


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def main():
    if "--restore-from" in sys.argv:
        sys.exit(restore_from(sys.argv[sys.argv.index("--restore-from") + 1]))
    ap = argparse.ArgumentParser()
    ap.add_argument("--app", required=True)
    ap.add_argument("--device", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--driver", required=True)
    ap.add_argument("--flutter", default="flutter", help='flutter command, e.g. "fvm flutter"')
    ap.add_argument("--platform", choices=["ios", "android"], default=None,
                    help="default: android if --device is a ready adb device, else ios")
    ap.add_argument("--grant", default="", help="permissions to grant up front: simctl names "
                    "(camera,microphone) on iOS, android.permission.* on Android")
    ap.add_argument("--app-id", "--bundle-id", dest="app_id", default="",
                    help="bundle id (iOS) / applicationId (Android); needed with --grant and on Android")
    ap.add_argument("--theme", choices=["light", "dark"], default=None,
                    help="set the device's light/dark mode for this run (put back afterwards); "
                         "default: leave it as it is (it is recorded in session.json either way)")
    ap.add_argument("--replace-installed", action="store_true",
                    help="Android: allow replacing an app already installed on the device")
    ap.add_argument("--build-timeout", type=int, default=None,
                    help="seconds until the app must connect (default 300 iOS, 600 Android)")
    ap.add_argument("--harness", required=True,
                    help="harness to copy in (kit/harness/generate.py output)")
    ap.add_argument("--dart-define", action="append", default=[],
                    help="passed to flutter test, e.g. DATA_SOURCE=api (repeatable)")
    ap.add_argument("--max-session", type=int, default=500,
                    help="hard cap in seconds for build + driver; cleanup always runs")
    a = ap.parse_args()
    if a.platform is None:
        ready = subprocess.run([kit_tools.adb(), "devices"], capture_output=True, text=True).stdout \
            if kit_tools.adb_path() else ""
        a.platform = "android" if f"{a.device}\tdevice" in ready else "ios"
    if a.build_timeout is None:
        a.build_timeout = 600 if a.platform == "android" else 300
    if not os.path.isfile(a.harness):
        sys.exit(f"REFUSED: harness {a.harness} not found")
    app, out = os.path.abspath(a.app), os.path.abspath(a.out)
    if _kit_state.inside(out, app):
        sys.exit(f"REFUSED: --out {out} is inside the app; the session would delete its own results "
                 f"(the app is restored to how it was). Use a folder outside the app.")
    os.makedirs(out, exist_ok=True)
    target = os.path.join(app, "integration_test", HARNESS_NAME)

    lock = f"/tmp/qa-harness-{a.device}.lock"
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
    except FileExistsError:
        pid = open(lock).read().strip()
        alive = pid.isdigit() and subprocess.run(["kill", "-0", pid],
                                                 capture_output=True).returncode == 0
        if alive:
            sys.exit(f"REFUSED: another QA run (pid {pid}) is using {a.device}")
        os.remove(lock)  # stale lock from a crashed run
        return main()

    # Ctrl-C / kill / closed terminal -> run the cleanup below
    for sig in (signal.SIGTERM, signal.SIGHUP):
        signal.signal(sig, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))

    if os.path.exists(pointer_file(app)):
        old = json.load(open(pointer_file(app)))["manifest"]
        os.remove(lock)
        sys.exit(f"REFUSED: an earlier run was stopped before it restored {app}. Run\n"
                 f"  python3 -I {os.path.abspath(__file__)} --restore-from {os.path.dirname(os.path.dirname(old))}\n"
                 f"then start again.")
    before_dirty, before_sum = dirty_files(app), checksum(app)
    try:
        manifest = take_backup(app, out)
    except Exception as e:  # noqa: BLE001
        os.remove(lock)
        sys.exit(f"REFUSED: could not back up the app's uncommitted files: {e}")
    sess = Session(out, a.device, a.platform)
    server = http.server.ThreadingHTTPServer(("127.0.0.1", free_port()), make_handler(sess))
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    flutter = driver = None
    summary = {"device": a.device, "platform": a.platform, "port": port,
               "started": time.strftime("%H:%M:%S"), "dart_defines": a.dart_define}
    dev_state = {"platform": a.platform, "device": a.device, "app_id": a.app_id}
    dev_state_path = os.path.join(out, "device_state.json")
    try:
        if os.path.exists(target):
            sys.exit(f"REFUSED: {target} already exists")
        grants = [g for g in a.grant.split(",") if g]
        if grants and not a.app_id:
            raise RuntimeError("--grant needs --app-id")
        if a.platform == "android":
            if not a.app_id:
                raise RuntimeError("Android needs --app-id (to check, grant and uninstall)")
            dev_state["was_installed"] = android_ready(a.device, a.app_id, a.replace_installed)
            json.dump(dev_state, open(dev_state_path, "w"))
            r = adb(a.device, "reverse", f"tcp:{port}", f"tcp:{port}")
            if r.returncode != 0:
                raise RuntimeError(f"adb reverse failed: {(r.stderr or r.stdout).strip()[:200]}")
            dev_state["reverse"] = f"tcp:{port}"
            json.dump(dev_state, open(dev_state_path, "w"))
            summary["was_installed"] = dev_state["was_installed"]
        dev_state["theme_before"] = get_theme(a.platform, a.device)
        if a.theme and a.theme != dev_state["theme_before"]:
            dev_state["theme_set"] = set_theme(a.platform, a.device, a.theme)
            if not dev_state["theme_set"]:
                raise RuntimeError(f"could not set the device to {a.theme} mode")
        json.dump(dev_state, open(dev_state_path, "w"))
        summary["theme"] = a.theme or dev_state["theme_before"]
        if dev_state.get("theme_set"):
            summary["theme_was"] = dev_state["theme_before"]
        shutil.copy(os.path.abspath(a.harness), target)
        if a.platform == "ios":
            subprocess.run(["xcrun", "simctl", "bootstatus", a.device, "-b"],
                           capture_output=True, timeout=180)
            for g in grants:  # a system permission dialog is outside Flutter: grant up front
                r = subprocess.run(["xcrun", "simctl", "privacy", a.device, "grant", g, a.app_id],
                                   capture_output=True, text=True)
                summary.setdefault("granted", {})[g] = r.returncode == 0 or r.stderr.strip()[:200]
        flog = open(os.path.join(out, "flutter_test.log"), "w")
        t0 = time.time()
        def launch():
            return subprocess.Popen(
                [*a.flutter.split(), "test", f"integration_test/{HARNESS_NAME}", "-d", a.device,
                 f"--dart-define=QA_PORT={port}", *[f"--dart-define={d}" for d in a.dart_define]],
                cwd=app, stdout=flog, stderr=subprocess.STDOUT, start_new_session=True)

        def stop(p):
            if p.poll() is None:
                os.killpg(p.pid, signal.SIGTERM)
                try:
                    p.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    os.killpg(p.pid, signal.SIGKILL)

        flutter, installed_at, relaunched = launch(), None, False
        while not sess.connected.wait(2):
            if flutter.poll() is not None:
                flog.flush()
                why = ""
                log = open(os.path.join(out, "flutter_test.log"), errors="replace").read()
                if "BUILD INTERRUPTED" in log:
                    why = (" — Xcode reported BUILD INTERRUPTED: usually another Xcode/flutter "
                           "build was running at the same time; run again when it is done")
                for k, v in ANDROID_FAILURES.items() if a.platform == "android" else ():
                    if k in log:
                        why = f" — {v}"
                        break
                raise RuntimeError(f"flutter test exited ({flutter.returncode}) before the "
                                   f"app connected{why}; see {out}/flutter_test.log")
            if a.platform == "android" and installed_at is None:
                flog.flush()
                if "Installing build/" in open(os.path.join(out, "flutter_test.log"), errors="replace").read():
                    installed_at = time.time()
            # Seen once on a TECNO phone: the app sat on the splash and flutter test never
            # started the test. Normally it connects ~10 s after install: restart once.
            if installed_at and time.time() - installed_at > 180 and not relaunched:
                print("app installed but the test did not start within 180 s (stuck on the "
                      "splash screen): restarting flutter test once", flush=True)
                summary["relaunched_stuck_test"] = True
                stop(flutter)
                flog.write("\n--- kit: restarted flutter test (stuck after install) ---\n")
                flog.flush()
                flutter, installed_at, relaunched = launch(), None, True
            if time.time() - t0 > a.build_timeout:
                raise RuntimeError("app did not connect within the build timeout")
        summary["build_and_launch_s"] = round(time.time() - t0, 1)
        if a.platform == "android":  # installed now: a permission dialog is outside Flutter
            for g in grants:
                r = adb(a.device, "shell", "pm", "grant", a.app_id, g)
                summary.setdefault("granted", {})[g] = r.returncode == 0 or (r.stderr or r.stdout).strip()[:200]
        print(f"app connected after {summary['build_and_launch_s']}s; running driver", flush=True)

        def watch():  # mark the app dead as soon as `flutter test` exits
            flutter.wait()
            sess.dead.set()
        threading.Thread(target=watch, daemon=True).start()

        env = dict(os.environ, QA_SERVER=f"http://127.0.0.1:{port}", QA_OUT=out, QA_PLATFORM=a.platform)
        t1 = time.time()
        driver = subprocess.Popen(a.driver, shell=True, env=env, start_new_session=True)
        while driver.poll() is None:
            if time.time() - t0 > a.max_session:
                os.killpg(driver.pid, signal.SIGTERM)
                summary["stopped_at_session_cap_s"] = a.max_session
                break
            if sess.dead.wait(1):
                time.sleep(3)  # let the driver read the 'app stopped' answer
                if driver.poll() is None:
                    os.killpg(driver.pid, signal.SIGTERM)
                summary["app_died_during_session"] = True
                break
        summary["driver_exit"] = driver.wait()
        summary["driver_s"] = round(time.time() - t1, 1)
    except KeyboardInterrupt:
        summary["interrupted"] = True
        print("interrupted: cleaning up", flush=True)
    except Exception as e:  # noqa: BLE001
        summary["error"] = str(e)
        print(f"ERROR: {e}", flush=True)
    finally:
        # Cleanup must finish: a second Ctrl-C / kill would otherwise abort the restore.
        for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
            signal.signal(sig, signal.SIG_IGN)
        for p in (driver,):
            if p and p.poll() is None:
                os.killpg(p.pid, signal.SIGTERM)
        if flutter and flutter.poll() is None:
            sess.steps.put({"action": "finish"})
            try:  # the harness ends itself on 'finish'; an app that never connected can't
                flutter.wait(timeout=45 if sess.connected.is_set() else 1)
            except subprocess.TimeoutExpired:
                os.killpg(flutter.pid, signal.SIGTERM)
                try:
                    flutter.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    os.killpg(flutter.pid, signal.SIGKILL)
        server.shutdown()
        if os.path.exists(target):
            os.remove(target)
        if a.platform == "android":
            summary["android_cleanup"] = android_cleanup(dev_state)
            dev_state["cleaned"] = True
        if restore_theme(dev_state) is not None:
            summary["theme_restored"] = dev_state["theme_restored"]
        json.dump(dev_state, open(dev_state_path, "w"))
        # 1) files that were not clean before: put back byte for byte
        restored, failed = restore_backup(manifest)
        summary["restored_uncommitted_files"] = restored
        # 2) tracked files that were clean before: git checkout (changed copy kept in after/)
        changed = sorted(set(status_paths(app)) - {e["path"] for e in manifest["entries"]})
        tracked = [f for f in changed if git(app, "ls-files", f).strip()]
        for f in tracked:
            keep_after(manifest["root"], app, f)
        if tracked:
            git(app, "checkout", "--", *tracked)
        summary["restored_tracked_files"] = tracked
        # 3) files the session created (e.g. ios/Podfile.lock from a first build): copy kept in after/
        new = [f for f in changed if f not in tracked and os.path.isfile(os.path.join(app, f))]
        for f in new:
            keep_after(manifest["root"], app, f)
            os.remove(os.path.join(app, f))
        summary["removed_new_files"] = new
        if failed:
            summary["RESTORE_FAILED"] = failed
            print("RESTORE FAILED — copies are in " + manifest["root"] + "/before:\n  "
                  + "\n  ".join(failed), flush=True)
        summary["app_unchanged"] = checksum(app) == before_sum and not failed
        if a.platform == "android":
            left = adb(a.device, "reverse", "--list").stdout
            summary["device_left_clean"] = f"tcp:{port}" not in left and (
                dev_state.get("was_installed") is not False or not
                adb(a.device, "shell", "pm", "path", a.app_id).stdout.startswith("package:"))
        summary["flutter_exit"] = flutter.returncode if flutter else None
        json.dump(summary, open(os.path.join(out, "session.json"), "w"), indent=2)
        if os.path.exists(lock):
            os.remove(lock)
        print("session:", json.dumps(summary), flush=True)
    if summary.get("RESTORE_FAILED"):
        sys.exit(2)


if __name__ == "__main__":
    main()
