# max-qa-kit

Claude Code plugin marketplace for SolGuruz. It holds one plugin, **max-qa-kit**:
AI testing for Flutter apps built on Max Architecture (SG CLI) with SDD spec
folders. It runs the app on the iOS simulator or an Android device, checks each
spec scenario screen by screen, and reports with screenshots. It never keeps
changes to the app.

## Install

```bash
claude plugin marketplace add https://github.com/vikaspsolguruz/max-qa-kit
claude plugin install max-qa-kit@solguruz
```

Then in Claude Code: `/max-qa-kit:qa <app folder> <feature>`, or the `qa-kit`
command (`qa-kit doctor` checks your machine first).

Details: [plugins/max-qa-kit/README.md](plugins/max-qa-kit/README.md) and
[plugins/max-qa-kit/GUIDE.md](plugins/max-qa-kit/GUIDE.md).
