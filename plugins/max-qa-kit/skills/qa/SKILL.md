---
name: qa
argument-hint: "[app folder] [feature] [scenario IDs] [on android]"
description: AI QA for a Max Architecture (SG CLI) Flutter app on the iOS simulator or a connected Android phone/emulator — checks a feature's spec scenarios (US1-AS1 …) by driving the real app and judging each screen, then writes a report with screenshots and accessibility findings. Use when the user says /qa <app> <feature>, asks to QA/test a feature or spec scenarios on a simulator or phone, or wants AI-checked coverage.
---

# /qa <app folder> <feature> [scenario IDs] [android|ios]

Checks spec scenarios on the running app. Works on any Max Architecture app with
spec folders (`specs/NNN-name/spec.md`). Every command below is `qa-kit`, the
kit's command (on PATH while this plugin is enabled; in the kit's own repo it is
`kit/bin/qa-kit`). Results go under `results/` in the current folder — except when
that folder is the app itself (Claude Code opened in the app): then the kit writes
them to `~/.qa-kit/results/<app folder>/…` and prints the path (the app is never
written to). Use the printed path when you open screenshots and reports.

## Steps

0. **Set up the app (first time; `plan` and `run` refresh it afterwards).**
   `qa-kit doctor` once per machine (flutter, git, claude, adb/xcrun, Jev key); fix
   anything MISSING as the "Missing tools" table below says.
   `qa-kit setup <app>` checks it is a Max app (exit 1 says what is missing) and
   writes its profile (`qa-kit where` shows where): start modes, fake-data switches,
   the team's test helpers, demo data, screens that open directly, iOS and Android
   permissions, the simulator and the connected Android device. "APP SETUP PROBLEM"
   lines are app findings to report (e.g. a permission used but not declared).
   Hand notes go in the profile's `notes` (tips, which data a screen shows); they
   survive re-runs.
1. **Pick the device.** Default is the iOS simulator. For Android add
   `--platform android` to `run` (or `--device <adb serial>`): the phone must be
   unlocked with USB debugging allowed. If the app is already installed on the
   phone the run is refused (flutter test may wipe its data); ask the user before
   adding `--replace-installed`. An app that was not installed is removed afterwards.
2. **Pick scenarios.** Feature = a spec folder (`006-find-customer` or
   `find-customer`). If the user gave no IDs, list the feature's scenarios with
   `qa-kit coverage <app> results/coverage/<app name>` and propose a batch of
   **at most 8** (each ~40–60 s). Prefer "story-tested" or "not covered".
3. **Plan** (one Claude call, ~$0.2–0.4):
   `qa-kit plan <app> <feature> results/qa/<name> --scenarios US1-AS1,US1-AS2`
   Read the printed plan: every checkpoint expectation must be visible on that
   moment's screen, use the spec's words, and name data only where the profile
   shows it. Each `start` must produce the Given (start modes, switches, `route:`
   starts). Fix `results/qa/<name>/qa_plan.json` by hand if needed.
4. **Run** (one session, ~$0.10–0.20 per scenario; a first build can take 3–10
   minutes, so give a first Android run `--max-session 900`):
   `qa-kit run <app> results/qa/<name> --judge jev+claude --max-session 480 [--platform android]`
   `--dart-define DATA_SOURCE=api` for a real backend. `--judge claude` only for comparisons.
5. **Verify before reporting.** For every `fail` or `cannot_tell`, open the
   checkpoint screenshot and the step JSON next to it. Decide: app bug, or tool
   problem (wrong test data, a check comparing two moments, a driver that noted the
   wrong step). Confirm app bugs in the source. Only report app bugs as bugs.
6. **Report** from `results/qa/<name>/qa_report.md`: verdict per scenario with one
   line of evidence, real bugs, accessibility findings (tappables with no label,
   tap areas under 48×48, text cut off), what was not checked and why, platform,
   and total cost. Refresh coverage with
   `qa-kit coverage <app> results/coverage/<app name> --qa-reports results/qa`.

No-AI check that the kit works on an app/device: `qa-kit smoke <app> [--platform android] [fresh relaunch route:<name> …]`.

## Missing tools — get them, from official sources only

When `qa-kit doctor` (or a failing run) shows a tool MISSING, fix it right there,
then continue the task:

| Missing | Do | Source |
|---|---|---|
| `adb` | `qa-kit get adb` | Google (sdkmanager, or dl.google.com platform-tools) |
| Gradle download fails on the first Android build ("Gradle threw an error while downloading") | `qa-kit get gradle <app>`, then run again | services.gradle.org, sha256-checked |
| no iPhone simulator | `qa-kit get ios-simulator` | creates one from the installed iOS runtime |
| no iOS runtime | ask the user first (several GB), then `qa-kit get ios-simulator --download-runtime` | Apple's `xcodebuild -downloadPlatform iOS` |
| `xcrun` (Xcode), `flutter`, `git`, `python3`, `claude` | `qa-kit get <name>` prints the official way; tell the user — these need their own installer or an App Store download | Apple / flutter.dev / python.org |

Say in one line what you are installing and from where before you run it. Never
install from other sources (unofficial mirrors, random Homebrew taps, copied
binaries), never use `sudo` for these, and never change the app's own build files
(Gradle version, plugins) to get around a missing tool.

## Rules

- Never commit, push, or edit the app. The session copies the generated harness
  in only while it runs and puts every uncommitted file back byte for byte;
  `session/session.json` must say `"app_unchanged": true` (on Android also
  `"device_left_clean": true`). If a run was killed: `qa-kit restore <out>/session`.
- One run at a time per device. Another Xcode build at the same time can interrupt
  an iOS run ("BUILD INTERRUPTED"): rerun.
- Screen text goes to Jev (TypeSafe) and screenshots to Claude: demo data only
  unless the user approves more. Confirm with the user before runs that cost more
  than a few dollars.
- A `pass` means the expectations were seen on screen, not that the scenario is
  bug-free; `not_checked` items are gaps, say so.
