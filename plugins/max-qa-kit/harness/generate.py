"""Build an app's QA harness from the generic template and the app's profile.

  generate.py <kit/apps/<app>/profile.json> <out.dart>

Fills qa_harness_template.dart's markers: package imports, every fixture
reset(), a typed setter for every fake-data switch (plus old names kept as
aliases in the profile notes), start modes (the team's seedX + driveToX pairs,
aliases, route: starts) and one case per callable test helper.
"""
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
TEMPLATE = os.path.join(HERE, "qa_harness_template.dart")

CONVERT = {"bool": "_qaBool", "int": "_qaInt", "double": "_qaDouble", "string": "_qaStr",
           "duration": "_qaDuration", "map_bool": "_qaMapBool", "list_string": "_qaStrings"}
ARG_KIND = {"String": "string", "int": "int", "double": "double", "bool": "bool",
            "List<String>": "list_string", "Duration": "duration"}
# A default we can copy into generated code as-is.
LITERAL = re.compile(r"^(true|false|null|-?\d+(\.\d+)?|'[^'$\\]*'|const Duration\([\w: ,]*\))$")


def dart_str(s):
    return "'" + s.replace("\\", "\\\\").replace("'", "\\'").replace("$", "\\$") + "'"


def switch_value(sw):
    """Dart expression turning step value `v` into the switch's type."""
    typ = sw["type"]
    if sw["kind"] == "enum":
        expr = f"{typ.rstrip('?')}.values.byName(_qaStr(v))"
    elif sw["kind"] == "set_string":
        expr = "_qaStrings(v).toSet()"
    else:
        expr = f"{CONVERT[sw['kind']]}(v)"
    return f"_qaNull(v) ? null : {expr}" if typ.endswith("?") else expr


def option_expr(p, where):
    """Named parameter value from the step's options map `o`."""
    conv = CONVERT[ARG_KIND[p["type"].rstrip("?")]]
    if p["required"]:
        return f"{conv}(_qaOpt(o, {dart_str(p['name'])}, {dart_str(where)}, {dart_str(p['type'])}))"
    default = p["default"] or "null"
    return f"o.containsKey({dart_str(p['name'])}) ? {conv}(o[{dart_str(p['name'])}]) : {default}"


def exposable(p):
    """A named/optional parameter we can expose: simple type and a copyable default."""
    return p["type"].rstrip("?") in ARG_KIND and (p["required"] or not p["default"]
                                                   or LITERAL.match(p["default"]))


def call_helper(prefix, h):
    """Dart lines calling helper h with args `a` / options `o` (inside a switch case)."""
    args = h["args"]
    pos = [p for p in args if p["kind"] == "positional"]
    opt = [p for p in args if p["kind"] == "optional"]
    named = [p for p in args if p["kind"] == "named" and exposable(p)]
    target = f"{prefix}.{h['name']}"
    base = ["tester"] if h["tester"] else []
    for i, p in enumerate(pos):
        conv = CONVERT[ARG_KIND[p["type"]]]
        base.append(f"{conv}(_qaArg(a, {i}, {dart_str(h['name'])}, {dart_str(p['type'] + ' ' + p['name'])}))")
    named_s = [f"{p['name']}: {option_expr(p, 'helper ' + h['name'])}" for p in named]
    lines = []
    # one call per number of optional positional arguments given (Dart can't skip them)
    for k in range(len(opt), -1, -1):
        extra = [f"{CONVERT[ARG_KIND[p['type']]]}(a[{len(pos) + j}])" for j, p in enumerate(opt[:k])]
        call = f"await {target}({', '.join(base + extra + named_s)});"
        if opt:
            n = len(pos) + k - 1
            cond = ("a.isNotEmpty" if n == 0 else f"a.length > {n}") if k else None
            lines.append(f"      {'if' if k == len(opt) else 'else if' if cond else 'else'}"
                         + (f" ({cond}) {{" if cond else " {"))
            lines.append(f"        {call}")
            lines.append("      }")
        else:
            lines.append(f"      {call}")
    return lines


def generate(profile):
    pkg = profile["app"]["package"]
    app = os.path.expanduser(profile["app"]["path"])
    imports = [f"import 'package:{pkg}/_app_initializer.dart';",
               f"import 'package:{pkg}/app/app.dart';",
               f"import 'package:{pkg}/core/local_storage/local_storage.dart';"]
    fm = os.path.join(app, "lib", "core", "fake_models")
    fixture_files = sorted({s["file"] for s in profile["switches"]} | {r["file"] for r in profile["resets"]})
    for f in fixture_files:
        imports.append(f"import 'package:{pkg}/core/fake_models/{f}';")
    # enum types can live in another fake_models file: import the file that declares each
    enum_types = {s["type"].rstrip("?") for s in profile["switches"] if s["kind"] == "enum"}
    if os.path.isdir(fm):
        for f in sorted(os.listdir(fm)):
            if f.endswith(".dart") and f not in fixture_files:
                text = open(os.path.join(fm, f), errors="replace").read()
                if any(re.search(rf"^enum\s+{t}\b", text, re.M) for t in enum_types):
                    imports.append(f"import 'package:{pkg}/core/fake_models/{f}';")

    modes = profile["start_modes"]
    callable_helpers = [h for h in profile["helpers"] if h["callable"]]
    helper_files = sorted({h["file"] for h in callable_helpers}
                          | {m["file"] for m in modes.values() if m["kind"] == "helpers"}
                          | {m["drive_file"] for m in modes.values() if m["kind"] == "helpers"})
    prefix = {f: f"h{i}" for i, f in enumerate(helper_files)}
    for f in helper_files:
        imports.append(f"import {dart_str(f)} as {prefix[f]};")

    g = ["void _resetFixtures() {"]
    g += [f"  {r['class']}.reset();" for r in profile["resets"]]
    g += ["}", "",
          "/// Fake-data switches found in lib/core/fake_models (name = `fixture.field`).",
          "void _setVariant(String name, Object? v) {", "  switch (name) {"]
    by_name = {s["name"]: s for s in profile["switches"]}
    for s in profile["switches"]:
        g += [f"    case {dart_str(s['name'])}:", f"      {s['class']}.{s['field']} = {switch_value(s)};"]
    aliases = profile.get("notes", {}).get("aliases", {})
    for alias, target in sorted(aliases.get("switches", {}).items()):
        if target in by_name and alias not in by_name:
            g += [f"    case {dart_str(alias)}: // old name kept in the profile notes",
                  f"      _setVariant({dart_str(target)}, v);"]
    g += ["    default:", "      throw StateError('no fake-data switch named \"$name\"');", "  }", "}", ""]

    g += ["/// Start modes from the team's seedX() + driveToX(tester) helpers.",
          "Future<void> _startMode(WidgetTester tester, String mode, Map<String, dynamic> o) async {",
          "  switch (mode) {"]
    helper_by_name = {h["name"]: h for h in profile["helpers"]}
    for name, m in modes.items():
        if m["kind"] != "helpers":
            continue
        seed = helper_by_name[m["seed"]]
        named = [p for p in seed["args"] if p["kind"] == "named" and exposable(p)]
        sargs = ", ".join(f"{p['name']}: {option_expr(p, 'start ' + name)}" for p in named)
        g += [f"    case {dart_str(name)}:",
              f"      await {prefix[m['file']]}.{m['seed']}({sargs});",
              f"      await {prefix[m['drive_file']]}.{m['drive']}(tester);"]
    for alias, t in sorted(aliases.get("start_modes", {}).items()):
        if t["mode"] in modes and alias not in modes:
            opts = ", ".join(f"{dart_str(k)}: {json.dumps(v)}" for k, v in t.get("options", {}).items())
            g += [f"    case {dart_str(alias)}: // old name kept in the profile notes",
                  f"      await _startMode(tester, {dart_str(t['mode'])}, {{{opts}, ...o}});"]
    names = ", ".join(["fresh", "relaunch", *[k for k in modes if k not in ("fresh", "relaunch")]])
    g += ["    default:", f"      throw StateError('unknown start mode $mode (known: {names})');",
          "  }", "}", ""]

    routes = {k[6:]: m["path"] for k, m in modes.items() if m["kind"] == "route"}
    g += ["/// Screens MyApp(initialRoute:) can open directly (top-level, no required arguments).",
          "Future<void> _startRoute(WidgetTester tester, String name) async {"]
    if routes:
        g += ["  const routes = <String, String>{"]
        g += [f"    {dart_str(k)}: {dart_str(v)}," for k, v in routes.items()]
        g += ["  };", "  final path = routes[name];",
              "  if (path == null) throw StateError('route:$name is not a direct start (see the profile)');",
              "  await tester.pumpWidget(MyApp(initialRoute: path));", "}", ""]
    else:
        g += ["  throw StateError('this app has no direct route starts');", "}", ""]

    g += ["/// The team's own integration-test helpers (callable ones only).",
          "Future<void> _helper(WidgetTester tester, String name, List<dynamic> a, Map<String, dynamic> o) async {",
          "  switch (name) {"]
    seen = {}
    for h in callable_helpers:
        key = h["name"] if h["name"] not in seen else f"{os.path.basename(h['file'])[:-5]}.{h['name']}"
        seen[h["name"]] = True
        g += [f"    case {dart_str(key)}:"] + call_helper(prefix[h["file"]], h)
    g += ["    default:", "      throw StateError('no callable helper named \"$name\"');", "  }", "}"]

    text = open(TEMPLATE).read()
    assert text.count("// @@IMPORTS@@") == 1 and text.count("// @@GENERATED@@") == 1
    header = (f"// GENERATED for {pkg} by kit/harness/generate.py from kit/apps/{pkg}/profile.json "
              f"— do not edit; re-run setup + generate.\n")
    return header + text.replace("// @@IMPORTS@@", "\n".join(imports)).replace("// @@GENERATED@@", "\n".join(g))


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit(__doc__)
    prof = json.load(open(sys.argv[1]))
    out = generate(prof)
    open(sys.argv[2], "w").write(out)
    print(f"wrote {sys.argv[2]} ({len(out.splitlines())} lines) for {prof['app']['package']}")
