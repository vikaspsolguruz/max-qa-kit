# AI QA kit for Max Architecture apps — guide

## In short

The kit checks a Flutter app built on our Max Architecture (SG CLI) the way a
tester would: it opens the real app on the iOS simulator or a connected Android
phone/emulator, follows the spec's
scenarios (`US1-AS1 …` in `specs/NNN-name/spec.md`), looks at every screen, and
says pass / fail / can't tell with a screenshot as proof. It works on any Max
app with no per-app code: it reads the app once (`setup`) and builds everything
else from that. It never commits or keeps changes to the app.

It is a Claude Code plugin (`max-qa-kit`, this folder): `qa-kit` is its command,
`/max-qa-kit:qa` and `/max-qa-kit:write-flow-test` its skills. Install and state
folders: `README.md`. In this repo, `kit/bin/qa-kit` runs the same command.

| Layer | What it answers | Cost | When |
|---|---|---|---|
| Coverage map | Which spec scenarios have a test at all? | free | any time |
| Team flow tests (Patrol / integration) | Does the flow still work? | free | every change |
| `/qa` (AI tester) | Does the screen match the spec's words? | ~$0.10–0.20 per scenario | new features, gaps |
| `/write-flow-test` | Turn an AI check into a free test | one Claude session | after a good `/qa` |
| Triage | Why did a test run fail, what to do next? | ~$0.0001, ~$0.06 if escalated | every red run |

## Setup (once per app, under a second)

```bash
qa-kit doctor                # once per machine: flutter, git, claude, adb/xcrun, Jev key
qa-kit check <app>           # is it a Max app the kit can use? (exit 0 = yes)
qa-kit setup <app>           # writes <package>/profile.json (`qa-kit where` shows the folder)
```

`setup` reads the app (read-only) and records:

- **start modes** — `fresh`, `relaunch`, one per `seedX()` + `driveToX(tester)` pair in
  the team's test helpers (e.g. `home`, with options like `owner=true`), and
  `route:<name>` for screens that open directly (`MyApp(initialRoute:)`);
  screens that need arguments are listed with the reason
- **fake-data switches** — every settable static field in `lib/core/fake_models`
  (enums, bools, delays, maps), e.g. `quotes.variant = error`
- **team helpers** — every `Future<void>` helper in `integration_test/**/*helper*.dart`
  the AI may call as one step (e.g. `openQuoteRow "Maria Santos"`); Patrol-only ones are listed, not called
- **demo data** per fixture file, so plans name only data that exists
- **permissions** used vs declared — iOS (`Info.plist`, `permission_handler` Podfile
  macros) and Android (the app's and its plugins' `AndroidManifest.xml`, as Gradle
  merges them) — the bundle id and Android `applicationId`, the simulator and the
  connected Android device, and storage the app keeps outside the boilerplate's
  `LocalStorage` (e.g. a drift database)

Lines starting `APP SETUP PROBLEM` are findings about the app itself. Hand
notes (tips, which data a screen shows, old names) go in the profile's
`notes` block and survive every re-run. `/qa` re-runs setup by itself before
each plan and run, and on first use of an app.

The contract the kit relies on in every Max app is in `kit/setup/MAX_CONTRACT.md`
(checked against the SG CLI boilerplate with `check_max.py --verify-boilerplate`).

## Coverage map

```bash
qa-kit coverage <app> results/coverage/<app> [--qa-reports results/qa]
```

Writes `coverage.html` + `coverage.json`. Each scenario is **scenario-tested**
(a test names it: `US1-AS3: …` or `(Sc.3)`), **ai-checked** (a `/qa` report
passed or failed it), **story-tested** (a test names only the story), or
**not covered**. A test is matched to its spec by the spec's own
`flutter test --name` commands, by being named in the spec docs, by its
file/folder name, or by its folder-mates. Specs the parser cannot read are
listed as "UNPARSED FORMAT", never counted as zero.

## `/qa` — the AI tester

In Claude Code: `/max-qa-kit:qa <app folder> <feature>`, or by hand:

```bash
qa-kit plan <app> <feature> results/qa/<name> --scenarios US1-AS1,US1-AS2
qa-kit run  <app> results/qa/<name> --judge jev+claude --max-session 480 [--platform android]
qa-kit smoke <app> [--platform android] [fresh relaunch route:<name> …]   # no-AI check
```

1. **Plan** (one Claude call, ~$0.06–0.40): per scenario, the start state, the
   steps, and small checks in the spec's own words at each moment to look at.
   Read it; fix `qa_plan.json` by hand if a check is wrong or a screen can't be
   reached.
2. **Run** (one session on the simulator or the phone): the app is built with a generated test
   harness; a capped Claude session drives each scenario through the real UI
   (taps, typing, scrolling, the team's helpers); every moment is captured —
   screenshot, visible text, the accessibility tree, tappables without a label,
   tap areas under 48×48.
3. **Judge**: Jev answers yes/no per check from the screen text and settles only
   clear passes; anything else goes to Claude with the screenshot.
4. **Report**: `qa_report.md` — verdict per scenario with evidence, what the
   driver noticed, accessibility findings, what was not checked and why, cost.

Every `fail` must be opened and confirmed before it is called a bug. Options:
`--platform android`, `--device <simulator udid | adb serial>`,
`--dart-define DATA_SOURCE=api` (real backend, below).

**Android.** The phone must be unlocked, with USB debugging allowed (some brands,
e.g. TECNO/Xiaomi, also need "Install via USB" in Developer options). The Mac is
reached over the USB cable (`adb reverse`), screenshots come from the phone, and
permissions the app declares are granted after install (`pm grant`). If the app
is already installed the run is refused, because `flutter test` may replace it and
wipe its data; `--replace-installed` accepts that. An app that was not installed
is removed afterwards, and `session.json` says `"device_left_clean": true`. The
first Android build of an app takes several minutes (Gradle).

**Theme.** Runs use the device's own light/dark setting, recorded as `theme` in
`session.json` (the test phone is dark, the simulator light, so a difference
between platforms may be the theme). `run.py --theme light|dark` sets it for one
run and puts it back.

**Safety.** The app is never edited for good: the harness file is copied in
only for the session, every uncommitted file is backed up byte for byte and put
back, build leftovers are removed, and `session/session.json` must say
`"app_unchanged": true`. Flutter itself sometimes edits a tracked file during a
build (e.g. "Upgrading analysis_options.yaml to exclude build and platform
directories" on sdd); that is undone too, with the edited copy kept in
`app_backup/after/`. If a run is killed hard, the next one refuses until
`qa-kit restore <out>/session` has put files back (and, on Android, removed the
port forward and the app).

## `/write-flow-test` — make an AI check free forever

After a scenario passes in `/qa`, `/max-qa-kit:write-flow-test <app> <feature> <ID>` writes
a normal `testWidgets('US1-AS2: …')` test in the app's own style (its helpers,
its fake-data switches), then proves it **passes** on the app and **fails**
when the bug it guards is planted — both through
`qa-kit guard`, which puts the app back byte for byte. The test lands
in `results/flowtests/`; adding it to the repo is the team's call.

## Backend switch — same tests, fake or real data

`kit/max/data_source.md`: one `fromSource(fake:, api:)` wrapper per repository
method, chosen at build time with `--dart-define=DATA_SOURCE=api`. Fake stays
the default (fast, never flaky); real staging runs nightly or before a release.
Needs from each project: staging URL per flavor, test accounts, a way past OTP.

## Triage — why is the run red?

```bash
qa-kit triage --log out.log --repo <app> [--diff-file x.diff | --range A...B] [--message "what I intended"]
```

Labels: `real_bug`, `outdated_test`, `broken_test`, `compile_error`,
`dependency_break`, `environment`, `flaky`, `rendering_drift`, each with a next
step. Jev answers two ways; when both agree its label is used (195/195 right in
testing), otherwise Claude decides. The commit message matters: the same
failure is `real_bug` under "[Refactor] …" and `outdated_test` under
"[Feature] remove company name per design".

## Results so far

**pencildrop** (28 specs, 457 scenarios, 153 team tests):

- Coverage: 100 scenario-tested, 27 AI-checked, 329 story-tested, 1 not covered.
- Planted-bug benchmark (12 bugs on 3 features, `kit/bench/`): team tests caught
  10/12; `/qa` caught 8/12 in both judge modes ($5.78 Claude-only vs $4.48
  Jev+Claude for all 12 runs); together 11/12 — `/qa` caught the wrong search
  hint the tests missed, the tests caught bugs outside the planned scenarios.
  On the 3 clean builds `/qa` raised no false alarm (its one fail there is the real missing job label).
- Real findings: the device-check card shows a failed line with a passing label;
  Set PIN submits on the 4th digit against spec 003; a missing job label on one
  quote row; unlabeled icon buttons; cut-off text.
- After making the kit generic, the 0002 find-customer run gives the same
  result: 4/4 pass, 36/36 individual checks identical ($0.53).
- On a real Android phone (TECNO LH7n, Android 14, dark mode) with the same plan:
  4/4 pass, 36/36 checks identical to the iOS run ($0.57); only difference: one
  customer name is cut off on the narrower phone.

**sdd** (11 specs, 92 scenarios, set up with zero app-specific code):

- Coverage: 0 of 92 scenarios have a test (the only integration test is the
  boilerplate's login example).
- `/qa` on spec 011 (accessibility) with per-checkpoint proof: teleprompter
  −/+ and camera buttons are 34×34 and unlabeled; the script card's delete icon
  is 20×20 and unlabeled; the add button is 40×40 and unlabeled; the back arrow
  has no label. Each confirmed in the source.
- `/qa` on spec 001 US1 stopped honestly: the script editor is still the
  `sg create screen` placeholder; the script list is linked from no screen.
- **sdd does not build for Android**: two plugins added in its uncommitted work
  (`disk_space`, `video_thumbnail`) still call `jcenter()`, which its Gradle 9.1
  no longer has. Setup now reports this; the Android build fails before any test.
  (Camera and microphone are fine on Android: the camera plugin declares them.)
- Setup found: camera and microphone are used but not declared for iOS and are
  compiled out of `permission_handler`, so the teleprompter camera can never
  start on iOS.

**SG CLI boilerplate** (affects every Max app): empty lists show the raw key
`noDataFound`; `CommonBackButton` has no screen-reader label; default icon
buttons are 40×40 and standard buttons 46 or 36 px tall, under the 48 px guideline.

## Gaps — what still needs a person

- **Things the screen can't show**: sound, haptics, colour contrast as numbers,
  the system share sheet, camera/microphone content, timing under real load.
- **Real-backend runs** need staging, test accounts and an OTP bypass per project.
- **Android emulators** are supported by the same code but were not tried; only a
  real phone (TECNO, Android 14) was.
- **"relaunch" is not a cold start**: it rebuilds the app in the same process.
- **Data in the app's own database** (e.g. drift) stays between scenarios of one
  run; each run starts from a fresh install.
- **Judging needs review**: a `pass` means the checks were seen, not that the
  scenario is bug-free; every `fail` is confirmed by hand before reporting.
- **CI** is not set up (needs a Mac runner with a simulator, or a Linux runner with an Android emulator).
- **An `sg` command**: the kit is a Claude Code plugin with its own `qa-kit` command;
  wiring it into the SG CLI itself is not done.

## Where things are

Plugin folder (`kit/` in the jev-ci-triage repo, `plugins/max-qa-kit/` in the
shared max-qa-kit repo): `bin/qa-kit` command · `.claude-plugin/` + `skills/` plugin ·
`state.py` where state lives · `setup/` contract + scanner + setup ·
`harness/` template, generator, `run.py` session runner, `step.py`, `guard.py` ·
`qa/` planner, driver, profile reader, `compare.py` · `judge/` Jev + Claude judge ·
`coverage/` coverage map · `triage/` triage · `max/` backend switch.
Profiles: `~/.qa-kit/apps/<app>/profile.json` (or `$QA_KIT_HOME`). Only in the
jev-ci-triage repo: `kit/apps/` (pencildrop, sdd), `kit/bench/` pencildrop
benchmark, `kit/examples/` retired hand-made pieces, `results/` every run's evidence.
