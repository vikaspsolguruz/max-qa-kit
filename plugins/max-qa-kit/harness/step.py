"""Send ONE step to the running app and print what is on screen.

Used by drivers (scripts or Claude) inside a run.py session; needs QA_SERVER.

CLI examples:
  python3 kit/harness/step.py start fresh
  python3 kit/harness/step.py start home
  python3 kit/harness/step.py start home owner=true          (start options: name=value)
  python3 kit/harness/step.py start route:findCustomer      (open a screen directly)
  python3 kit/harness/step.py helper openQuoteRow "Maria Santos"   (a team test helper)
  python3 kit/harness/step.py helper enterCo -- 2 "x=1"       (after --: all positional)
  python3 kit/harness/step.py tap text "Log in"
  python3 kit/harness/step.py tap semantics "Back"
  python3 kit/harness/step.py tap icon U+EB0B        (from 'tappable with no label')
  python3 kit/harness/step.py tap point 195 830
  python3 kit/harness/step.py enter_text type TextFormField "+15551234567"
  python3 kit/harness/step.py scroll down 400
  python3 kit/harness/step.py scroll_to text "Clear"
  python3 kit/harness/step.py variant quotes error
  python3 kit/harness/step.py back | wait 2000 | capture
  python3 kit/harness/step.py json '{"action":"tap","target":{"key":"x"}}'
Target kinds: text, text_contains, key, semantics (exact label), semantics_contains,
type, tooltip
(add --index N to pick the Nth match).
"""
import json
import os
import re
import sys
import urllib.request

OPTION = re.compile(r"^[A-Za-z_]\w*=\S*$")  # name=value, no spaces (a start/helper option)
TARGET_KINDS = {"text", "text_contains", "key", "semantics", "semantics_contains", "type", "tooltip", "icon"}


def send(step, wait_s=120):
    step = dict(step, wait_s=wait_s)
    req = urllib.request.Request(os.environ["QA_SERVER"] + "/step",
                                 data=json.dumps(step).encode(), method="POST",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=wait_s + 10) as r:
        return json.load(r)


def parse(argv):
    index = None
    if "--index" in argv:
        i = argv.index("--index")
        index = int(argv[i + 1])
        argv = argv[:i] + argv[i + 2:]
    act, rest = argv[0], argv[1:]
    if act == "json":
        return json.loads(rest[0])
    if act == "start":
        opts = dict(x.split("=", 1) for x in rest[1:] if OPTION.match(x))
        step = {"action": "start", "mode": rest[0] if rest else "fresh"}
        return {**step, "options": opts} if opts else step
    if act == "helper":
        # positional arguments in order; name=value for named options
        # `--` ends options: everything after it is a positional argument
        head, tail = (rest[1:rest.index("--")], rest[rest.index("--") + 1:]) if "--" in rest else (rest[1:], [])
        args = [x for x in head if not OPTION.match(x)] + tail
        opts = dict(x.split("=", 1) for x in head if OPTION.match(x))
        return {"action": "helper", "name": rest[0], "args": args, "options": opts}
    if act == "tap" and rest[0] == "point":
        return {"action": "tap", "target": {"point": [float(rest[1]), float(rest[2])]}}
    if act in ("tap", "long_press", "scroll_to"):
        t = {rest[0]: rest[1]}
    elif act == "enter_text":
        t, text = {rest[0]: rest[1]}, rest[2]
    elif act == "scroll":
        return {"action": "scroll", "direction": rest[0] if rest else "down",
                "pixels": int(rest[1]) if len(rest) > 1 else 300}
    elif act == "variant":
        return {"action": "variant", "name": rest[0], "variant": rest[1]}
    elif act == "wait":
        return {"action": "wait", "ms": int(rest[0]) if rest else 1000}
    elif act in ("back", "capture", "finish"):
        return {"action": act}
    else:
        raise SystemExit(f"unknown action {act}")
    if list(t)[0] not in TARGET_KINDS:
        raise SystemExit(f"target kind must be one of {sorted(TARGET_KINDS)}")
    if index is not None:
        t["index"] = index
    step = {"action": act, "target": t}
    if act == "enter_text":
        step["text"] = text
    return step


def show(r):
    """Compact, readable screen summary for a driver (human or Claude)."""
    lines = [f"step {r.get('n')}: {r.get('status', '?').upper()}"
             + (f" — {r['error']}" if r.get("error") else "")]
    if r.get("app_exception"):
        lines.append(f"APP EXCEPTION: {r['app_exception']}")
    lines.append(f"screens: {', '.join(r.get('screens') or []) or '-'}")
    lines.append(f"focused: {r.get('focused') or '-'}   keyboard requested: "
                 f"{r.get('keyboard_requested')}   keyboard inset: {r.get('keyboard_inset')}")
    lines.append("visible text:")
    lines += [f"  {t}" for t in r.get("texts", [])[:80]]
    lines.append("accessibility:")
    lines += [f"  {s}" for s in r.get("semantics", [])[:80]]
    if r.get("no_label_tappables"):
        lines.append("tappable with no label (tap by icon, or by point):")
        lines += [f"  icon {x['icon']}  {x['where']}  point {x['point']}  size {x['size']}"
                  for x in r["no_label_tappables"]]
    if r.get("small_tappables"):
        lines.append("tap area under 48x48:")
        lines += [f"  {x['label']}  {x['where']}  point {x['point']}  area {x['tap_area']}"
                  for x in r["small_tappables"]]
    if r.get("screenshot"):
        lines.append(f"screenshot: {r['screenshot']}")
    return "\n".join(lines)


if __name__ == "__main__":
    print(show(send(parse(sys.argv[1:]))))
