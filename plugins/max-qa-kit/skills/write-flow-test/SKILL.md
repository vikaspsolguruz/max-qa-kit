---
name: write-flow-test
argument-hint: "[app folder] [feature] [scenario ID]"
description: Turn a scenario that /qa checked on a simulator or phone into a normal integration (flow) test in the app's own style, so it runs for free on every change. Use when the user asks to lock in a /qa result, convert an AI-checked scenario into a Patrol/integration test, or close a coverage gap with a real test.
---

# /write-flow-test <app folder> <feature> <scenario ID>

Input: a `/qa` report (`results/qa/<name>/qa_report.json`) where the scenario
passed, the app's profile (`qa-kit where` shows the folder; `<package>/profile.json`),
and the app's own tests and docs. `qa-kit` is the kit's command (in the kit's
own repo: `kit/bin/qa-kit`).

## Steps

1. **Read the evidence.** From the report take the checkpoints, expectations,
   and the step captures (`session/step_NNN.json`) the driver used: they show
   the exact taps, texts and labels that worked, and the start mode / switches.
2. **Read the house style first.** The app's testing docs if it has them
   (e.g. `docs/testing.md`), the feature's existing test file, and the helpers
   listed in the profile (`helpers`: seeds without a tester, `driveTo…`/action
   helpers with one). Reuse helpers; never re-implement login or navigation
   that a helper already does. Use the same test style the app uses
   (`testWidgets` or `patrolTest`); with no helpers, start the way the profile's
   start mode does (`LocalStorage.clear()`, `initializeApp()`, `MyApp(...)`).
3. **Write the test** in the feature's folder, named with the scenario ID so
   the coverage map counts it as scenario-tested:
   `testWidgets('US1-AS2: <what it proves>', (tester) async { ... })`.
   - Set fake data the way the report's start did (the profile's `switches`
     name the fixture class and field to set; reset with the fixture `reset()`).
   - Timed `pump(Duration)` waits, not `pumpAndSettle`, unless the app's own
     tests do otherwise.
   - One `expect` per expectation from the report; check state the way the
     existing tests do (`find.text`, `getSemantics(...).flagsCollection`,
     bloc state) — not screenshots.
   - Expectations that were "not checked" by /qa stay out, or get a comment.
4. **Prove it both ways** — always through the guard, which puts every
   uncommitted file of the app back byte for byte and removes the test after.
   Device: the profile's simulator udid (`app.device.udid`) or Android serial
   (`app.android_device.serial`; on Android uninstall the app afterwards with
   `adb -s <serial> uninstall <android id>` if it was not installed before):
   - passes: `qa-kit guard --app <app> --out results/flowtests/<name>/pass --put <test file>=integration_test/<feature>/<file> -- flutter test integration_test/<feature>/<file> -d <device>`
     → exit 0;
   - catches the bug: plant the bug the test is about as a one-line change
     inside the guarded command (the guard undoes it), e.g.
     `-- sh -c "sed -i '' 's/old/new/' lib/x.dart && flutter test …"` → must
     fail with a message that names the problem;
   - `guard.json` must say `"app_unchanged": true` both times.
   - A tool missing (`adb`, a simulator, a Gradle download failing)? Use
     `qa-kit get <tool>` exactly as the `/qa` skill's "Missing tools" table says
     (official sources only).
5. **Hand over** the test file (kept in `results/flowtests/`) and the
   pass/fail proof. Adding it to the app's repo is the team's call.
