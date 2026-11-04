"""Plain /qa and /write-flow-test in Claude Code's suggestions.

Plugin skills always show with the plugin's prefix (/max-qa-kit:qa). A personal skill
(~/.claude/skills/<name>/SKILL.md) shows as /<name>, so this writes two small personal
skills that hand over to the plugin's skills. They hold no instructions of their own, so
they never go stale when the plugin updates.

  qa-kit shortcuts            add /qa and /write-flow-test (never overwrites a skill you made)
  qa-kit shortcuts --remove   remove them (only the ones this command made)
"""
import os
import sys

MARK = "<!-- managed by max-qa-kit: qa-kit shortcuts (remove with: qa-kit shortcuts --remove) -->"
SKILLS = {
    "qa": ("Shortcut for /max-qa-kit:qa — AI QA of a Max Architecture app's spec scenarios "
           "on the iOS simulator or an Android device.",
           "[app folder] [feature] [scenario IDs] [on android]"),
    "write-flow-test": ("Shortcut for /max-qa-kit:write-flow-test — turn a scenario /qa passed "
                        "into a normal flow test.",
                        "[app folder] [feature] [scenario ID]"),
}


def body(name, description, hint):
    return f"""---
name: {name}
description: {description}
argument-hint: "{hint}"
disable-model-invocation: true
---
{MARK}

Use the Skill tool to run the `max-qa-kit:{name}` skill with these arguments: $ARGUMENTS

Then follow that skill exactly. If no skill named `max-qa-kit:{name}` is available, tell
the user the max-qa-kit plugin is not installed or enabled
(`claude plugin install max-qa-kit@solguruz`).
"""


def main(argv):
    if argv and argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    root = os.path.join(os.environ.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~/.claude"), "skills")
    remove = "--remove" in argv
    code = 0
    for name, (desc, hint) in SKILLS.items():
        path = os.path.join(root, name, "SKILL.md")
        ours = os.path.isfile(path) and MARK in open(path, errors="replace").read()
        if remove:
            if ours:
                os.remove(path)
                if not os.listdir(os.path.dirname(path)):
                    os.rmdir(os.path.dirname(path))
                print(f"removed /{name}")
            elif os.path.exists(path):
                print(f"left /{name} alone: {path} was not made by qa-kit")
            continue
        if os.path.exists(path) and not ours:
            print(f"skipped /{name}: you already have {path} (not made by qa-kit); "
                  f"use /max-qa-kit:{name} instead")
            code = 1
            continue
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(body(name, desc, hint))
        print(f"{'updated' if ours else 'added'} /{name} -> /max-qa-kit:{name}  ({path})")
    if not remove:
        print("start a new Claude Code session (or run /reload-plugins) to see them in the / menu")
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
