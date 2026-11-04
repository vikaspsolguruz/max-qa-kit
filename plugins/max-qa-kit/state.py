"""Where the kit keeps its state (never inside the kit/plugin folder when installed).

  $QA_KIT_HOME            if set: <home>/apps/<package>/profile.json, <home>/.env, <home>/history/
  dev checkout            this repo (kit/ next to scripts/scenarios.py, not under
                          ~/.claude/plugins): kit/apps/, <repo>/.env, <repo>/history/
  otherwise (installed)   ~/.qa-kit/ with the same layout as $QA_KIT_HOME

The Jev key: $TYPESAFE_API_KEY, else TYPESAFE_API_KEY=... in the .env above.
"""
import os

KIT = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(KIT)


def dev_checkout():
    installed = os.path.realpath(KIT).startswith(
        os.path.realpath(os.path.expanduser("~/.claude/plugins")) + os.sep)
    return not installed and os.path.isfile(os.path.join(REPO, "scripts", "scenarios.py"))


def home():
    if os.environ.get("QA_KIT_HOME"):
        return os.path.abspath(os.path.expanduser(os.environ["QA_KIT_HOME"]))
    return REPO if dev_checkout() else os.path.expanduser("~/.qa-kit")


def apps_dir():
    if not os.environ.get("QA_KIT_HOME") and dev_checkout():
        return os.path.join(KIT, "apps")
    return os.path.join(home(), "apps")


def env_file():
    return os.path.join(home(), ".env")


def history_dir():
    return os.path.join(home(), "history")


def jev_key(path=None):
    """$TYPESAFE_API_KEY, else the .env file (given, or the state home's)."""
    if os.environ.get("TYPESAFE_API_KEY"):
        return os.environ["TYPESAFE_API_KEY"].strip()
    path = path or env_file()
    if os.path.isfile(path):
        for line in open(path):
            if line.startswith("TYPESAFE_API_KEY="):
                return line.split("=", 1)[1].strip()
    raise SystemExit(f"TYPESAFE_API_KEY missing: set it in the environment or in {path} "
                     f"(chmod 600), or run the judge as --judge claude")


def results_dir():
    return os.path.join(home(), "results")


def inside(path, folder):
    path, folder = os.path.realpath(path), os.path.realpath(folder)
    return path == folder or path.startswith(folder + os.sep)


def out_dir(app, out, quiet=False):
    """Where to write results. The app under test is never written to: an output folder
    inside the app (e.g. `results/...` while Claude Code runs in the app's folder) is moved,
    always to the same place, to <state>/results/<app folder>/<rest of the path>."""
    o = os.path.abspath(os.path.expanduser(out))
    if not inside(o, app):
        return o
    parts = os.path.relpath(os.path.realpath(o), os.path.realpath(app)).split(os.sep)
    if parts and parts[0] == "results":
        parts = parts[1:]
    new = os.path.join(results_dir(), os.path.basename(os.path.realpath(app)), *[p for p in parts if p != "."])
    if not quiet:
        print(f"note: {out} is inside the app; results go to {new} (the app stays untouched)", flush=True)
    return new
