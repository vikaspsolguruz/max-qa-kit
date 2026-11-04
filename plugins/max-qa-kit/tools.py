"""Find the command-line tools the kit uses, and get missing ones from official sources only.

  qa-kit get adb                 Android platform-tools (adb) from Google
  qa-kit get gradle <app>        the Gradle version the app's wrapper wants, from services.gradle.org,
                                 sha256-checked, put where the wrapper looks (a first Android build
                                 otherwise downloads it itself and can fail on a slow network)
  qa-kit get ios-simulator [--download-runtime]
                                 an iPhone simulator: created from an installed iOS runtime; with
                                 --download-runtime also fetches the runtime from Apple (several GB)
  qa-kit get xcode | flutter | git | python | claude
                                 cannot be installed safely by a script: prints the official way

Every download is HTTPS from the vendor (Google, Gradle, Apple) and is checked before use
(sha256 where the vendor publishes one, otherwise the tool must run). Nothing is installed
from mirrors or third-party taps.
"""
import glob
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile

MANUAL = {
    "xcode": "Install Xcode from the Mac App Store (https://apps.apple.com/app/xcode/id497799835) or "
             "https://developer.apple.com/xcode/, open it once to finish setup, then run "
             "`sudo xcode-select -s /Applications/Xcode.app`.",
    "flutter": "Install Flutter from https://docs.flutter.dev/get-started/install (use the version "
               "the app's team uses; check its README / .fvmrc).",
    "git": "Run `xcode-select --install` (Apple's command-line tools include git), or install Xcode.",
    "python": "Run `xcode-select --install` (includes python3), or get it from https://www.python.org/downloads/.",
    "claude": "Install Claude Code: https://code.claude.com/docs/en/setup",
}


def sdk_dirs():
    """Android SDK folders, in the order Android tools look for them."""
    out = [os.environ.get("ANDROID_HOME"), os.environ.get("ANDROID_SDK_ROOT"),
           os.path.expanduser("~/Library/Android/sdk"), os.path.expanduser("~/Android/Sdk")]
    return [d for d in dict.fromkeys(out) if d]


def adb_path():
    """adb on PATH, else inside an Android SDK folder (Android Studio does not add it to PATH)."""
    found = shutil.which("adb")
    if found:
        return found
    for d in sdk_dirs():
        p = os.path.join(d, "platform-tools", "adb")
        if os.access(p, os.X_OK):
            return p
    return None


def adb():
    """The adb command to run ("adb" if none is found, so errors still name it)."""
    return adb_path() or "adb"


def which(tool):
    if tool == "adb":
        return adb_path()
    return shutil.which(tool)


def fix_hint(tool):
    return {"adb": "qa-kit get adb", "xcrun": "qa-kit get xcode", "flutter": "qa-kit get flutter",
            "git": "qa-kit get git", "claude": "qa-kit get claude", "python3": "qa-kit get python"}.get(tool, "")


def curl(url, dest):
    """Resumable HTTPS download (curl ships with macOS); retries on a reset connection."""
    for _ in range(8):
        r = subprocess.run(["curl", "-fL", "--progress-bar", "--proto", "=https", "--retry", "5", "--retry-all-errors",
                            "-C", "-", "-o", dest, url])
        if r.returncode == 0:
            return True
    return False


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def get_adb():
    have = adb_path()
    if have:
        print(f"adb already available: {have}")
        if not shutil.which("adb"):
            print(f"  (not on PATH; the kit finds it anyway. To use it yourself add "
                  f"{os.path.dirname(have)} to PATH)")
        return 0
    sdk = sdk_dirs()[0] if os.environ.get("ANDROID_HOME") or os.environ.get("ANDROID_SDK_ROOT") \
        else os.path.expanduser("~/Library/Android/sdk" if sys.platform == "darwin" else "~/Android/Sdk")
    sdkmanager = next(iter(glob.glob(os.path.join(sdk, "cmdline-tools", "*", "bin", "sdkmanager"))), None)
    if sdkmanager:
        print(f"installing platform-tools with Google's sdkmanager ({sdkmanager})")
        r = subprocess.run([sdkmanager, "platform-tools"])
        if r.returncode == 0 and adb_path():
            print(f"adb ready: {adb_path()}")
            return 0
    osname = {"darwin": "darwin", "linux": "linux"}.get(sys.platform)
    if not osname:
        print("unsupported OS for the automatic download; see "
              "https://developer.android.com/tools/releases/platform-tools")
        return 1
    url = f"https://dl.google.com/android/repository/platform-tools-latest-{osname}.zip"
    print(f"downloading Android platform-tools from Google: {url}\n"
          f"(Android SDK terms: https://developer.android.com/studio/terms)")
    tmp = tempfile.mkdtemp(prefix="qa-kit-adb-")
    z = os.path.join(tmp, "platform-tools.zip")
    if not curl(url, z):
        print("download failed")
        return 1
    with zipfile.ZipFile(z) as zf:
        bad = [n for n in zf.namelist() if n.startswith("/") or ".." in n.split("/")]
        if bad or not all(n.startswith("platform-tools/") for n in zf.namelist()):
            print("unexpected files in the archive; not installing")
            return 1
        os.makedirs(sdk, exist_ok=True)
        if os.path.exists(os.path.join(sdk, "platform-tools")):
            print(f"{sdk}/platform-tools exists but has no working adb; not overwriting it")
            return 1
        zf.extractall(sdk)
    p = os.path.join(sdk, "platform-tools", "adb")
    os.chmod(p, 0o755)
    r = subprocess.run([p, "version"], capture_output=True, text=True)
    if r.returncode != 0:
        print(f"installed adb does not run: {r.stderr[:200]}")
        return 1
    shutil.rmtree(tmp, ignore_errors=True)
    print(f"adb ready: {p} ({r.stdout.splitlines()[0]})\n  the kit finds it there; to use it "
          f"yourself add {os.path.dirname(p)} to PATH")
    return 0


def gradle_dist_dir(url):
    """Where the Gradle wrapper looks for a distribution: base-36 MD5 of its URL."""
    n = int(hashlib.md5(url.encode()).hexdigest(), 16)
    digits, s = "0123456789abcdefghijklmnopqrstuvwxyz", ""
    while n:
        n, r = divmod(n, 36)
        s = digits[r] + s
    name = url.rsplit("/", 1)[-1][:-len(".zip")]
    home = os.environ.get("GRADLE_USER_HOME") or os.path.expanduser("~/.gradle")
    return os.path.join(home, "wrapper", "dists", name, s or "0")


def get_gradle(app):
    app = os.path.abspath(os.path.expanduser(app))
    props_path = os.path.join(app, "android", "gradle", "wrapper", "gradle-wrapper.properties")
    if not os.path.isfile(props_path):
        print(f"no {props_path}: not a Flutter app with an android/ folder")
        return 1
    props = open(props_path).read()
    m = re.search(r"^distributionUrl=(.+)$", props, re.M)
    if not m:
        print("no distributionUrl in gradle-wrapper.properties")
        return 1
    url = m.group(1).strip().replace("\\:", ":")
    if not re.match(r"^https://services\.gradle\.org/distributions/gradle-[\w.\-]+\.zip$", url):
        print(f"distributionUrl is not the official Gradle server ({url}); not downloading it")
        return 1
    dist = gradle_dist_dir(url)
    name = url.rsplit("/", 1)[-1]
    if os.path.exists(os.path.join(dist, name + ".ok")):
        print(f"Gradle {name} already installed: {dist}")
        return 0
    want = (re.search(r"^distributionSha256Sum=(\w+)", props, re.M) or [None, None])[1]
    if not want:
        r = subprocess.run(["curl", "-fsL", "--proto", "=https", url + ".sha256"],
                           capture_output=True, text=True)
        want = r.stdout.strip() if r.returncode == 0 else None
    if not want or not re.fullmatch(r"[0-9a-f]{64}", want):
        print("could not get Gradle's published sha256; not downloading unverified")
        return 1
    os.makedirs(dist, exist_ok=True)
    tmp = os.path.join(dist, name + ".qa-kit-part")
    print(f"downloading {url} (resumable) into {dist}")
    if not curl(url, tmp):
        print("download failed; run the same command again to resume")
        return 1
    got = sha256(tmp)
    if got != want:
        os.remove(tmp)
        print(f"sha256 mismatch (got {got}, want {want}); deleted")
        return 1
    for leftover in (name + ".part",):
        if os.path.exists(os.path.join(dist, leftover)):
            os.remove(os.path.join(dist, leftover))
    os.replace(tmp, os.path.join(dist, name))
    print(f"Gradle {name} ready (sha256 ok); the next Android build unpacks it")
    return 0


def get_ios_simulator(download_runtime=False):
    if not shutil.which("xcrun"):
        print(MANUAL["xcode"])
        return 1
    devs = json.loads(subprocess.run(["xcrun", "simctl", "list", "devices", "available", "-j"],
                                     capture_output=True, text=True).stdout or "{}").get("devices", {})
    iphones = [d for v in devs.values() for d in v if d["name"].startswith("iPhone")]
    if iphones:
        print(f"iPhone simulator available: {iphones[0]['name']} ({iphones[0]['udid']})")
        return 0
    rts = [r for r in json.loads(subprocess.run(["xcrun", "simctl", "list", "runtimes", "available", "-j"],
                                                capture_output=True, text=True).stdout or "{}").get("runtimes", [])
           if r.get("platform") == "iOS" or r["identifier"].split(".")[-1].startswith("iOS")]
    if not rts:
        if not download_runtime:
            print("No iOS simulator runtime is installed. Apple's download is several GB; to get it run\n"
                  "  qa-kit get ios-simulator --download-runtime\n"
                  "(runs `xcodebuild -downloadPlatform iOS`, Apple's own downloader), or use "
                  "Xcode > Settings > Components.")
            return 1
        print("downloading the iOS simulator runtime with Apple's xcodebuild (several GB)")
        if subprocess.run(["xcodebuild", "-downloadPlatform", "iOS"]).returncode != 0:
            return 1
        return get_ios_simulator(False)
    rt = sorted(rts, key=lambda r: [int(x) for x in re.findall(r"\d+", r.get("version", "0"))])[-1]
    types = [t for t in rt.get("supportedDeviceTypes", []) if t.get("productFamily") == "iPhone"]
    if not types:
        print(f"runtime {rt['name']} lists no iPhone device types")
        return 1
    t = types[-1]
    r = subprocess.run(["xcrun", "simctl", "create", f"{t['name']} (qa-kit)", t["identifier"],
                        rt["identifier"]], capture_output=True, text=True)
    if r.returncode != 0:
        print(f"could not create a simulator: {r.stderr[:200]}")
        return 1
    print(f"created simulator {t['name']} (qa-kit) on {rt['name']}: {r.stdout.strip()}")
    return 0


def main(argv):
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    tool = argv[0].lower()
    if tool == "adb":
        return get_adb()
    if tool == "gradle":
        if len(argv) < 2:
            print("usage: qa-kit get gradle <app folder>")
            return 2
        return get_gradle(argv[1])
    if tool in ("ios-simulator", "simulator"):
        return get_ios_simulator("--download-runtime" in argv)
    if tool in ("xcrun", "simctl"):
        tool = "xcode"
    if tool in ("python3",):
        tool = "python"
    if tool in MANUAL:
        print(MANUAL[tool])
        return 0
    print(f"unknown tool {tool!r}\n{__doc__}")
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
