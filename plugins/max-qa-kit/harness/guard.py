"""Run one command inside an app with its files protected byte for byte.

  guard.py --app <app> --out <dir> [--put SRC=DEST ...] -- <command ...>

Before: every modified / deleted / untracked file of the app is backed up
(same code as run.py). --put copies extra files in (e.g. a test to try).
After (also on Ctrl-C / kill): those files are removed, everything is put back,
tracked files that were clean are checked out, files the command created are
removed (copies kept in <out>/app_backup/after/). Exit code = the command's,
or 2 if a restore failed. Used by /write-flow-test to try a test in the app.
"""
import json
import os
import shutil
import signal
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import run as R  # noqa: E402


def main(argv):
    if "--" not in argv:
        raise SystemExit(__doc__)
    opts, cmd = argv[:argv.index("--")], argv[argv.index("--") + 1:]
    app = os.path.abspath(os.path.expanduser(opts[opts.index("--app") + 1]))
    out = R.kit_state_out(app, opts[opts.index("--out") + 1])
    puts = [opts[i + 1].split("=", 1) for i, o in enumerate(opts) if o == "--put"]
    os.makedirs(out, exist_ok=True)
    if os.path.exists(R.pointer_file(app)):
        raise SystemExit(f"REFUSED: an earlier run did not restore {app}; run run.py --restore-from first")
    before = R.checksum(app)
    manifest = R.take_backup(app, out)
    placed, code = [], None
    for sig in (signal.SIGTERM, signal.SIGHUP):
        signal.signal(sig, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    try:
        for src, dest in puts:
            target = os.path.join(app, dest)
            if os.path.exists(target):
                raise SystemExit(f"REFUSED: {dest} already exists in the app")
            os.makedirs(os.path.dirname(target), exist_ok=True)
            shutil.copy(os.path.expanduser(src), target)
            placed.append(target)
        code = subprocess.run(cmd, cwd=app).returncode
    except KeyboardInterrupt:
        print("interrupted: restoring", flush=True)
    finally:
        for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
            signal.signal(sig, signal.SIG_IGN)
        for t in placed:
            if os.path.exists(t):
                os.remove(t)
        restored, failed = R.restore_backup(manifest)
        known = {e["path"] for e in manifest["entries"]}
        changed = [f for f in R.status_paths(app) if f not in known]
        tracked = [f for f in changed if R.git(app, "ls-files", f).strip()]
        for f in changed:
            R.keep_after(manifest["root"], app, f)
        if tracked:
            R.git(app, "checkout", "--", *tracked)
        new = [f for f in changed if f not in tracked and os.path.isfile(os.path.join(app, f))]
        for f in new:
            os.remove(os.path.join(app, f))
        summary = {"command": cmd, "exit": code, "restored": restored + tracked, "removed_new_files": new,
                   "restore_failed": failed, "app_unchanged": R.checksum(app) == before and not failed}
        json.dump(summary, open(os.path.join(out, "guard.json"), "w"), indent=1)
        print("guard:", json.dumps(summary), flush=True)
    return 2 if summary["restore_failed"] else (code if code is not None else 1)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
