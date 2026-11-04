# max-qa-kit

Claude Code plugin marketplace for SolGuruz. It holds one plugin, **max-qa-kit**:
AI testing for Flutter apps built on Max Architecture (SG CLI) with SDD spec
folders (`specs/NNN-name/spec.md`). It runs the app on the iOS simulator or an
Android device, checks each spec scenario screen by screen, and reports with
screenshots. It never keeps changes to the app.

## Before you install

You need:

| What | Why | Check |
|---|---|---|
| Claude Code | runs the plugin | `claude --version` |
| python3 3.10+ | the kit's scripts | `python3 --version` |
| Flutter + git | build the app, back it up and restore it | `flutter --version` |
| Xcode (macOS) | iOS simulator runs | `xcrun simctl list devices` |
| Android SDK `adb` | Android phone / emulator runs | `adb devices` |
| SG CLI | setup compares the app with the Max boilerplate (optional, more precise) | `ls ~/.pub-cache/git/sg_cli-*` |

You need iOS or Android, not both.

## Install

```bash
claude plugin marketplace add https://github.com/vikaspsolguruz/max-qa-kit
claude plugin install max-qa-kit@solguruz
```

Or inside Claude Code: `/plugin marketplace add https://github.com/vikaspsolguruz/max-qa-kit`,
then `/plugin install max-qa-kit@solguruz`. Restart Claude Code (or start a new
session) so it loads.

## Check it works

In a new Claude Code session, in any folder, ask Claude to run `qa-kit doctor`.
It lists flutter, git, claude, adb, xcrun and the Jev key, and says what is
missing. The skills show up as `/max-qa-kit:qa` and `/max-qa-kit:write-flow-test`.

Outside Claude Code, `qa-kit` is not on your PATH; run it by path:
`~/.claude/plugins/cache/solguruz/max-qa-kit/<version>/bin/qa-kit`.

## Jev key (optional, makes judging cheaper)

The `/qa` judge asks Jev (TypeSafe) first and Claude only when Jev is unsure.
Without a key `/qa` still works: add `--judge claude` to `qa-kit run` (it just
costs more per scenario). `qa-kit triage` needs the key.

**1. Get a key.** Log in (or sign up) at the TypeSafe console and create a key on
the keys page: https://console.typesafe.ai/keys. A team can also share one key:
whoever owns the team's TypeSafe account creates it and sends it privately (a
password manager or DM, never the repo or a public channel).

**2. Save it** in the kit's key file (recommended: Claude Code sessions always see it):

```bash
mkdir -p ~/.qa-kit
echo "TYPESAFE_API_KEY=<your key>" > ~/.qa-kit/.env
chmod 600 ~/.qa-kit/.env
```

Or set it in your shell profile (`~/.zshrc`): `export TYPESAFE_API_KEY=<your key>`.
The kit reads the environment variable first, then `~/.qa-kit/.env`
(or `$QA_KIT_HOME/.env` if you set `QA_KIT_HOME`).

**3. Check it:** `qa-kit doctor` should say `jev key  found`.

Never commit the key or paste it into a chat, issue or screenshot.

## First use

```bash
qa-kit setup ~/code/my_app            # reads the app once (read-only), writes ~/.qa-kit/apps/<app>/profile.json
qa-kit smoke ~/code/my_app            # free check that the kit can start the app (add --platform android for a phone)
```

Then in Claude Code: `/max-qa-kit:qa ~/code/my_app <feature>`. Results land in
`results/` in the folder you run from.

Android: unlock the phone and allow USB debugging. Some brands (TECNO, Xiaomi)
also need "Install via USB" in Developer options. If the app is already installed
on the phone the run is refused, because the test build could wipe its data.

## Update

```bash
claude plugin marketplace update solguruz
claude plugin update max-qa-kit@solguruz
```

## Uninstall

```bash
claude plugin uninstall max-qa-kit@solguruz
claude plugin marketplace remove solguruz
rm -rf ~/.qa-kit          # optional: your app profiles, Jev key file and triage history
```

## More

- [plugins/max-qa-kit/README.md](plugins/max-qa-kit/README.md): commands, where state lives, safety
- [plugins/max-qa-kit/GUIDE.md](plugins/max-qa-kit/GUIDE.md): how each part works, results so far, known gaps
