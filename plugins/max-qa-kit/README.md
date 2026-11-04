# Max QA kit (Claude Code plugin)

AI testing for Flutter apps built on SolGuruz Max Architecture (SG CLI) with SDD
spec folders (`specs/NNN-name/spec.md`). It opens the real app on the iOS
simulator or a connected Android phone/emulator, follows the spec's scenarios
(`US1-AS1 …`), looks at every screen, and says pass / fail / can't tell with a
screenshot as proof. It works on any Max app with no per-app code, and never
commits or keeps changes to the app.

## What you get

| | What it answers | Cost |
|---|---|---|
| `qa-kit coverage` | Which spec scenarios have a test at all? | free |
| `/max-qa-kit:qa` | Does the screen match the spec's words? Accessibility (unlabeled buttons, tap areas under 48×48)? | ~$0.10–0.20 per scenario |
| `/max-qa-kit:write-flow-test` | Turn a passed AI check into a normal flow test | one Claude session |
| `qa-kit triage` | Why did a test run fail, and what should the dev do next? | ~$0.0001 (~$0.06 if Claude is asked) |
| `qa-kit smoke` | Does the kit start this app on this device? | free |

## Needs

- macOS for the iOS simulator (Xcode); Android: `adb` and a phone with USB
  debugging allowed (some brands also need "Install via USB" in Developer options),
  or an emulator.
- `python3` (3.10+), `flutter`, `git`, and the `claude` CLI (plans, drives and judges).
- The SG CLI installed (`~/.pub-cache/git/sg_cli-*`): setup compares the app with its
  `max` boilerplate (it still works without it, slightly less precisely).
- Optional: a TypeSafe key for the Jev judge (get one at https://console.typesafe.ai/keys;
  put `TYPESAFE_API_KEY=<key>` in `~/.qa-kit/.env` with chmod 600, or export it).
  Without it use `--judge claude`. Setup steps: the repo README, "Jev key".

Run `qa-kit doctor` to check. Missing `adb`, an iPhone simulator, or a Gradle download
that keeps failing? `qa-kit get adb`, `qa-kit get ios-simulator`, `qa-kit get gradle <app>`
fetch them from the official source (Google, Apple, gradle.org) and verify them; for
Xcode, Flutter, git or python it prints the official installer. The `/qa` skill does this
by itself when a tool is missing.

## Install

```bash
claude plugin marketplace add https://github.com/vikaspsolguruz/max-qa-kit
claude plugin install max-qa-kit@solguruz
```

Updates: `claude plugin marketplace update solguruz`.

The plugin puts `qa-kit` on the Bash PATH inside Claude Code and adds the skills
`/max-qa-kit:qa` and `/max-qa-kit:write-flow-test`. Outside Claude Code run it
by path: `~/.claude/plugins/…/max-qa-kit/bin/qa-kit` (`qa-kit where` prints it).

## Use

```bash
qa-kit setup ~/code/my_app                     # once: reads the app (read-only), writes its profile
qa-kit coverage ~/code/my_app results/coverage/my_app
qa-kit plan ~/code/my_app 006-find-customer results/qa/find --scenarios US1-AS1,US1-AS2
qa-kit run  ~/code/my_app results/qa/find --judge jev+claude --max-session 480 [--platform android]
qa-kit smoke ~/code/my_app --platform android fresh relaunch
qa-kit triage --log out.log --repo ~/code/my_app --message "what I intended"
```

Or in Claude Code: `/max-qa-kit:qa ~/code/my_app find-customer`.

Results are written under `results/` in the folder you run from.

## Where state lives

Profiles (`<package>/profile.json`, with a hand-editable `notes` block that
survives re-runs), the Jev key file and triage history live in `$QA_KIT_HOME`,
else `~/.qa-kit/` — never inside the plugin folder (it is replaced on update).
`qa-kit where` prints the paths.

## Safety

- The app is never edited for good: a generated test harness is copied into
  `integration_test/` only during a session, and every uncommitted file is backed
  up byte for byte and put back. `session/session.json` must say
  `"app_unchanged": true`. After a hard kill the next run refuses until
  `qa-kit restore <out>/session`.
- Android: the run is refused if the app is already installed on the phone
  (flutter test may replace it and wipe its data) unless you pass
  `--replace-installed`; an app that was not installed is uninstalled afterwards,
  and the USB port forward is removed (`"device_left_clean": true`).
- Screen text goes to Jev (TypeSafe) and screenshots to Claude: use demo data.

More detail: `GUIDE.md` in this folder.
