"""Read a Max Architecture app and list what the QA harness can use. Read-only.

  scan.py <app> [--json]

Sections (filled step by step in plan 0003):
  switches   settable static fields in lib/core/fake_models/*.dart  (step 2.1)
  helpers    Future<void> helpers in integration_test/**/*helper*.dart  (step 2.2)
  test_data  readable values per fixture file and static member         (step 2.3)
  routes     route names + whether MyApp(initialRoute:) can open them   (step 2.5)
"""
import json
import os
import re
import sys

TOP_DECL = re.compile(r"^(?:(?:abstract|final|sealed|base|interface|mixin)\s+)*"
                      r"(class|enum|mixin|extension)\s+(\w+)")
# `static int? x;` (no default) counts; getters `static int get x => ...` do not.
FIELD = re.compile(r"^\s+static\s+(?!const\b|final\b|late\b)([\w<>, ?]+?)\s+(\w+)\s*(?:=(?!>)\s*(.+?))?;\s*$")
SETTER = re.compile(r"^\s+static\s+set\s+(\w+)\(\s*([\w<>, ?]+?)\s+\w+\s*\)")
FIELD_START = re.compile(r"^\s+static\s+(?!const\b|final\b|late\b)([\w<>, ?]+?)\s+(\w+)\s*=(?!>)")
RESET = re.compile(r"^\s+static\s+void\s+reset\(\)")

SIMPLE = {"bool": "bool", "int": "int", "double": "double", "String": "string",
          "Duration": "duration", "Map<String, bool>": "map_bool",
          "List<String>": "list_string", "Set<String>": "set_string"}


def short(cls):
    """FakeCustomerFixtures -> customer, FakeQuoteDetailFixtures -> quoteDetail."""
    s = re.sub(r"^Fake", "", cls)
    s = re.sub(r"(Fixtures|Data|Model)$", "", s) or cls
    return s[0].lower() + s[1:]


def parse_enums(text):
    enums = {}
    for m in re.finditer(r"^enum\s+(\w+)\s*\{(.*?)\}", text, re.M | re.S):
        body = m.group(2).split(";")[0]                     # enhanced enums: values before ';'
        values = [re.sub(r"\(.*", "", v).strip() for v in body.split(",")]
        enums[m.group(1)] = [v for v in values if re.fullmatch(r"\w+", v)]
    return enums


def scan_switches(app):
    folder = os.path.join(app, "lib", "core", "fake_models")
    out = {"switches": [], "resets": [], "skipped": [], "files": 0}
    if not os.path.isdir(folder):
        return out
    texts = {}
    for f in sorted(os.listdir(folder)):
        if f.endswith(".dart"):
            texts[f] = open(os.path.join(folder, f), errors="replace").read()
    out["files"] = len(texts)
    enums = {}
    for t in texts.values():
        enums.update(parse_enums(t))
    for f, t in texts.items():
        current = None
        for n, line in enumerate(t.splitlines(), 1):
            d = TOP_DECL.match(line)
            if d:
                current = d.group(2) if d.group(1) in ("class", "mixin") else None
                continue
            if current is None:
                continue
            if RESET.match(line):
                out["resets"].append({"class": current, "file": f, "line": n})
                continue
            st = SETTER.match(line)
            if st:  # a static setter is settable too, e.g. FakeHomeFixtures.waitingItemCount
                typ, name, default = st.group(2).strip(), st.group(1), None
                where = {"class": current, "field": name, "file": f, "line": n, "via": "setter"}
                kind = SIMPLE.get(typ.rstrip("?"))
                entry = {**where, "type": typ, "default": default, "name": f"{short(current)}.{name}"}
                if kind:
                    out["switches"].append({**entry, "kind": kind})
                elif typ in enums:
                    out["switches"].append({**entry, "kind": "enum", "values": enums[typ]})
                else:
                    out["skipped"].append({**where, "type": typ, "why": "type not settable from a step"})
                continue
            m = FIELD.match(line)
            if not m:
                part = FIELD_START.match(line)
                if part and not line.rstrip().endswith(";"):  # never miss one silently
                    out["skipped"].append({"class": current, "field": part.group(2), "file": f,
                                           "line": n, "type": part.group(1).strip(),
                                           "why": "default spans several lines"})
                continue
            typ, name = m.group(1).strip(), m.group(2)
            default = m.group(3).strip() if m.group(3) else None
            where = {"class": current, "field": name, "file": f, "line": n}
            if name.startswith("_"):
                out["skipped"].append({**where, "type": typ, "why": "private"})
                continue
            kind = SIMPLE.get(typ.rstrip("?"))
            entry = {**where, "type": typ, "default": default,
                     "name": f"{short(current)}.{name}"}
            if kind:
                out["switches"].append({**entry, "kind": kind})
            elif typ in enums:
                out["switches"].append({**entry, "kind": "enum", "values": enums[typ]})
            else:
                out["skipped"].append({**where, "type": typ, "why": "type not settable from a step"})
    return out


HELPER_DECL = re.compile(r"^(\s*)(static\s+)?Future<(.+?)>\s+(\w+)\s*\(")
TESTERS = {"WidgetTester": "widget", "PatrolIntegrationTester": "patrol", "PatrolTester": "patrol"}
ARG_KINDS = {"String": "string", "int": "int", "double": "double", "bool": "bool",
             "List<String>": "list_string", "Duration": "duration"}


def split_top(text, sep=","):
    """Split on `sep` outside (), [], {}, <>."""
    out, depth, cur = [], 0, ""
    for ch in text:
        if ch in "([{<":
            depth += 1
        elif ch in ")]}>":
            depth -= 1
        if ch == sep and depth == 0:
            out.append(cur)
            cur = ""
        else:
            cur += ch
    if cur.strip():
        out.append(cur)
    return [x.strip() for x in out if x.strip()]


def matched(sig):
    """Text inside the first balanced (...) of `sig`, or None if it is not closed yet."""
    depth = 0
    for k, ch in enumerate(sig):
        depth += (ch == "(") - (ch == ")")
        if depth == 0:
            return sig[1:k]
    return None


def parse_params(text):
    """`WidgetTester t, String a, [Duration w = ...], {bool x = false, required int y}`
    -> [{name, type, kind: positional|optional|named, required, default}]"""
    params = []
    for part in split_top(text):
        group = None
        if part[0] in "[{" and part[-1] in "]}":
            group = "optional" if part[0] == "[" else "named"
            inner = split_top(part[1:-1])
        else:
            inner = [part]
        for q in inner:
            req = q.startswith("required ")
            q = re.sub(r"^required\s+", "", q)
            q, _, default = q.partition("=")
            words = q.strip().rsplit(None, 1)
            typ, name = (words[0], words[1]) if len(words) == 2 else ("dynamic", words[0])
            params.append({"name": name, "type": typ.strip(), "kind": group or "positional",
                           "required": group is None or req,
                           "default": default.strip() or None})
    return params


def scan_helpers(app):
    """Every Future<void> helper the team wrote for integration tests, and whether the
    harness can call it: seeds (no tester), tester helpers with simple arguments,
    Patrol-only / other ones listed with the reason."""
    root = os.path.join(app, "integration_test")
    out = {"helpers": [], "pairs": [], "ignored": [], "helper_files": 0}
    if not os.path.isdir(root):
        return out
    paths = sorted(os.path.join(d, f) for d, _, fs in os.walk(root) for f in fs
                   if f.endswith(".dart") and "helper" in f)
    out["helper_files"] = len(paths)
    for path in paths:
        rel = os.path.relpath(path, root)
        lines = open(path, errors="replace").read().splitlines()
        cls = None
        for n, line in enumerate(lines, 1):
            d = TOP_DECL.match(line)
            if d:
                cls = d.group(2) if d.group(1) in ("class", "mixin", "extension") else None
            m = HELPER_DECL.match(line)
            if not m:
                continue
            indent, static, ret, name = m.groups()
            owner = cls if indent else None
            if indent and not static:
                out["ignored"].append({"name": f"{owner}.{name}", "file": rel, "line": n,
                                       "why": "instance method, no object to call it on"})
                continue
            sig, i = line[m.end() - 1:], n - 1
            body = matched(sig)
            while body is None and i + 1 < len(lines):  # parameter list spans lines
                i += 1
                sig += " " + lines[i].strip()
                body = matched(sig)
            if body is None:
                out["ignored"].append({"name": name, "file": rel, "line": n,
                                       "why": "parameter list not closed"})
                continue
            call = f"{owner}.{name}" if owner else name
            where = {"name": call, "file": rel, "line": n}
            if ret.strip() != "void":
                out["ignored"].append({**where, "why": f"returns {ret.strip()}, not an action"})
                continue
            if name.startswith("_"):
                out["ignored"].append({**where, "why": "private"})
                continue
            params = parse_params(body)
            tester = TESTERS.get(params[0]["type"]) if params else None
            args = params[1:] if tester else params
            entry = {**where, "class": owner, "tester": tester, "args": args}
            bad = [a for a in args if a["type"] not in ARG_KINDS]
            if tester == "patrol":
                entry.update(role="patrol_only", callable=False,
                             why="needs a Patrol tester; the harness runs a plain integration test")
            elif params and params[0]["type"] not in TESTERS and any(
                    "Tester" in a["type"] for a in params):
                entry.update(role="other", callable=False, why="tester is not the first parameter")
            elif bad:
                entry.update(role="other", callable=False,
                             why="argument " + ", ".join(f"{a['name']} ({a['type']})" for a in bad)
                                 + " cannot be given from a step")
            else:
                for a in args:
                    a["arg_kind"] = ARG_KINDS[a["type"]]
                entry.update(role="tester" if tester else "seed", callable=True)
            out["helpers"].append(entry)
    seeds = {h["name"][4:]: h for h in out["helpers"]
             if h["role"] == "seed" and re.match(r"seed[A-Z]", h["name"])}
    for h in out["helpers"]:
        m = re.fullmatch(r"driveTo([A-Z]\w*)", h["name"])
        if h["role"] == "tester" and m and m.group(1) in seeds:
            out["pairs"].append({"name": m.group(1), "seed": seeds[m.group(1)]["name"],
                                 "drive": h["name"]})
    return out


STATIC_MEMBER = re.compile(r"^(\s+)static\s+(?:const\s+|final\s+)?(?:[\w<>?, ]+?\s+)?(?:get\s+)?(\w+)\s*(=>|=|\{|\()")
PAIR = re.compile(r"(?:'([^'\n]+)'|\"([^\"\n]+)\"|\b(\w+))\s*:\s*(?:'([^'\\\n]*)'|\"([^\"\\\n]*)\")")
STRING = re.compile(r"'([^'\\\n]*)'|\"([^\"\\\n]*)\"")


def readable(v):
    """Text a user could see or type: names, labels, codes, phones — not ids or state tokens."""
    v = v.strip()
    if not v or "$" in v:
        return bool(v) and bool(re.search(r"[A-Z ]", re.sub(r"\$\{?\w*\}?", "", v)))
    return bool(re.search(r"[A-Z ]|@", v) or re.fullmatch(r"\+?[\d][\d ()-]{2,}", v))


def rows_in(text):
    """Map / constructor literals with `key: 'value'` pairs -> rows [{key: value}].
    A nested literal (e.g. an address inside a customer) merges into its parent row as
    `parent.key`; a literal whose parent has no pairs of its own is a row by itself."""
    rows = []
    stack = [{"buf": [], "kids": []}]

    def own_pairs(body):
        row = {}
        for m in PAIR.finditer(body):
            k = m.group(1) or m.group(2) or m.group(3)
            v = m.group(4) if m.group(4) is not None else m.group(5)
            if readable(v) or not re.fullmatch(r"\w+", k):
                row[k] = v
        return row

    i = 0
    while i < len(text):
        ch = text[i]
        if ch in "'\"":
            j = i + 1
            while j < len(text) and text[j] != ch and text[j] != "\n":
                j += 2 if text[j] == "\\" else 1
            stack[-1]["buf"].append(text[i:j + 1])
            i = j + 1
            continue
        if ch in "{(":
            stack.append({"buf": [], "kids": []})
        elif ch in "})" and len(stack) > 1:
            f = stack.pop()
            row = own_pairs("".join(f["buf"]))
            if row:
                for key, kid in f["kids"]:
                    row.update({(f"{key}.{k}" if key else k): v for k, v in kid.items()})
                kids = [("", row)]
            else:
                kids = f["kids"]  # e.g. a list or function body: pass rows up unchanged
            before = "".join(stack[-1]["buf"])
            km = re.search(r"(?:'([^']+)'|(\w+))\s*:\s*$", before)
            key = (km.group(1) or km.group(2)) if km and row else ""
            stack[-1]["kids"].extend((key, kid) for _, kid in kids)
            stack[-1]["buf"].append("_")
        else:
            stack[-1]["buf"].append(ch)
        i += 1
    for f in stack:
        rows.extend(kid for _, kid in f["kids"])
    return rows


def scan_test_data(app):
    """Readable values the fake data serves, per fixture file and static member, so plans
    and the driver only use data that exists (e.g. the names in the customer list)."""
    folder = os.path.join(app, "lib", "core", "fake_models")
    out = []
    if not os.path.isdir(folder):
        return {"test_data": out}
    for f in sorted(os.listdir(folder)):
        if not f.endswith(".dart"):
            continue
        lines = open(os.path.join(folder, f), errors="replace").read().splitlines()
        starts = [(n, m.group(1), m.group(2)) for n, l in enumerate(lines)
                  if (m := STATIC_MEMBER.match(l))]
        members = []
        for k, (n, indent, name) in enumerate(starts):
            end = starts[k + 1][0] if k + 1 < len(starts) else len(lines)
            for e in range(n + 1, end):  # stop at the end of the class
                if re.match(r"^\S", lines[e]):
                    end = e
                    break
            text = "\n".join(lines[n:end])
            rows = rows_in(text)
            entry = {"member": name, "line": n + 1}
            if rows:
                entry["rows"] = rows
            else:
                vals = [a if a is not None else b for a, b in STRING.findall(text)]
                vals = list(dict.fromkeys(v for v in vals if readable(v)))
                if not vals:
                    continue
                entry["values"] = vals
            members.append(entry)
        if members:
            out.append({"file": f, "members": members})
    return {"test_data": out}


def scan_routes(app):
    """Routes from lib/app/app_routes/_route_names.dart, joined with their route config,
    the list that registers them (screen / bottom sheet / dialog) and their arguments.
    Flutter (navigator.dart defaultGenerateInitialRoutes) opens a top-level '/x' initial
    route as the first page; a nested 'parent/child' name is pushed alone, without its
    parent (e.g. a tab without the tab bar), so only top-level screens are direct starts."""
    rd = os.path.join(app, "lib", "app", "app_routes")
    names_text = read_text(os.path.join(rd, "_route_names.dart"))
    names = re.findall(r"static const (?:String\s+)?(\w+)\s*=\s*['\"]([^'\"]*)['\"]\s*;", names_text)
    default = re.search(r"static String initialRoute = (\w+);", names_text)
    cfg = read_text(os.path.join(rd, "app_route_config.dart"))
    config_of, args_of = {}, {}
    for m in re.finditer(r"class (\w+) extends AppRouteConfig<(\w+)>(.*?)\n}", cfg, re.S):
        r = re.search(r"routeName => Routes\.(\w+);", m.group(3))
        if r:
            config_of[r.group(1)], args_of[r.group(1)] = m.group(1), m.group(2)
    lists = {kind: read_text(os.path.join(rd, f)) for kind, f in
             (("screen", "screen_routes.dart"), ("bottom_sheet", "bottom_sheet_routes.dart"),
              ("dialog", "dialog_routes.dart"))}
    args_text = read_text(os.path.join(rd, "route_arguments.dart"))
    routes = []
    for field, value in names:
        conf = config_of.get(field)
        kind = next((k for k, t in lists.items() if conf and re.search(rf"\b{conf}\(", t)), None)
        arg_cls = args_of.get(field)
        required, optional = [], []
        c = re.search(rf"const {arg_cls}\((.*?)\);", args_text, re.S) if arg_cls else None
        if c:
            for q in parse_params(c.group(1)):
                (required if q["required"] else optional).append(q["name"].replace("this.", ""))
        top = value.startswith("/")
        why = (None if top and kind == "screen" and not required else
               "not registered as a route" if not kind else
               f"is a {kind.replace('_', ' ')}, not a screen" if kind != "screen" else
               f"needs arguments: {', '.join(required)}" if required else
               f"nested under '{value.split('/')[0]}': opening it alone skips its parent")
        routes.append({"name": field, "path": value, "top_level": top, "kind": kind,
                       "config": conf, "arguments": arg_cls, "required_args": required,
                       "optional_args": optional,
                       "direct_start": why is None, "why_not": why})
    return {"routes": routes, "default_route": default.group(1) if default else None}


def read_text(path):
    return open(path, errors="replace").read() if os.path.isfile(path) else ""


def package_name(app):
    m = re.search(r"^name:\s*(\w+)", open(os.path.join(app, "pubspec.yaml")).read(), re.M)
    return m.group(1) if m else None


def scan(app):
    app = os.path.abspath(os.path.expanduser(app))
    return {"app": app, "package": package_name(app), **scan_switches(app), **scan_helpers(app),
            **scan_test_data(app), **scan_routes(app)}


def summary(r):
    from collections import Counter
    kinds = Counter(s["kind"] for s in r["switches"])
    print(f"app {r['app']} (package {r['package']})")
    print(f"switches: {len(r['switches'])} in {r['files']} fake_models files — "
          + ", ".join(f"{k} {v}" for k, v in kinds.most_common()))
    print(f"reset() classes: {len(r['resets'])}; skipped: "
          + (", ".join(f"{s['class']}.{s['field']} ({s['why']})" for s in r["skipped"]) or "none"))
    roles = Counter(h["role"] for h in r["helpers"])
    print(f"helpers: {len(r['helpers'])} in {r['helper_files']} files — "
          + ", ".join(f"{k} {v}" for k, v in roles.most_common())
          + f"; seed/driveTo pairs: {', '.join(p['name'] for p in r['pairs']) or 'none'}")
    for h in r["helpers"]:
        sig = ", ".join(f"{a['type']} {a['name']}" + (f" = {a['default']}" if a["default"] else "")
                        + ("" if a["kind"] == "positional" else f" [{a['kind']}]") for a in h["args"])
        print(f"   {h['role']:11} {h['name']}({sig})" + ("" if h["callable"] else f"  — not callable: {h['why']}"))
    print(f"test data: {len(r['test_data'])} fixture files")
    for fd in r["test_data"]:
        parts = []
        for m in fd["members"]:
            if "rows" in m:
                keys = Counter(k for row in m["rows"] for k in row)
                parts.append(f"{m['member']} {len(m['rows'])} rows ({', '.join(k for k, _ in keys.most_common(4))})")
            else:
                parts.append(f"{m['member']} = {', '.join(m['values'][:3])}" + (" …" if len(m["values"]) > 3 else ""))
        print(f"   {fd['file']}: " + "; ".join(parts))
    ok = [x for x in r["routes"] if x["direct_start"]]
    print(f"routes: {len(r['routes'])} (default {r['default_route']}); direct starts {len(ok)}: "
          + ", ".join(x["name"] for x in ok))
    for x in r["routes"]:
        if not x["direct_start"]:
            print(f"   not direct: {x['name']} ({x['path']}) — {x['why_not']}")
    if r["ignored"]:
        print(f"ignored: {len(r['ignored'])} (" + ", ".join(f"{i['name']}: {i['why']}" for i in r["ignored"]) + ")")


if __name__ == "__main__":
    r = scan(sys.argv[1])
    print(json.dumps(r, indent=2)) if "--json" in sys.argv else summary(r)
