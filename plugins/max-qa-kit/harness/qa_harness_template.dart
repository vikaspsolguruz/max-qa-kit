// QA harness for a Max Architecture app — generic template.
//
// kit/harness/generate.py fills the @@ markers from the app's profile
// (kit/apps/<app>/profile.json): package imports, fixture resets, fake-data
// switches, start modes and the team's own test helpers. The result is copied
// into integration_test/ ONLY while kit/harness/run.py runs, then removed.
// Never commit it. It asks a server on the Mac for one step at a time, runs it
// with normal Flutter finders, and sends back what is on screen.
//
// Follows the Max test rules: start with pumpWidget + timed pump, never
// pumpAndSettle; reset state like the app's own tests (LocalStorage.clear,
// fixture reset()).
import 'dart:convert';
import 'dart:io';
import 'dart:ui' show Rect, CheckedState, Tristate;

import 'package:flutter/material.dart';
import 'package:flutter/rendering.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:integration_test/integration_test.dart';
// @@IMPORTS@@

const _port = int.fromEnvironment('QA_PORT');
const _idleLimit = Duration(minutes: 5);

void main() {
  final binding = IntegrationTestWidgetsFlutterBinding.ensureInitialized();
  binding.framePolicy = LiveTestWidgetsFlutterBindingFramePolicy.fullyLive;

  testWidgets('qa harness', (tester) async {
    final semantics = tester.ensureSemantics();
    final client = HttpClient()..connectionTimeout = const Duration(seconds: 5);
    var lastStep = DateTime.now();
    var n = 0;
    while (true) {
      Map<String, dynamic> step;
      try {
        step = await _get(client, '/next');
      } catch (_) {
        break; // server is gone: end quietly
      }
      final action = step['action'] as String? ?? 'noop';
      if (action == 'noop') {
        if (DateTime.now().difference(lastStep) > _idleLimit) break;
        continue;
      }
      lastStep = DateTime.now();
      if (action == 'finish') {
        await _post(client, '/result', {'n': ++n, 'step': step, 'status': 'ok'});
        break;
      }
      n++;
      var status = 'ok';
      String? error;
      try {
        final note = await _run(tester, step);
        if (note != null) error = note;
      } catch (e) {
        status = 'failed';
        error = e.toString().split('\n').take(3).join(' ').trim();
      }
      final appError = tester.takeException();
      final capture = _capture(tester);
      await _post(client, '/result', {
        'n': n,
        'step': step,
        'status': status,
        'error': ?error,
        'app_exception': ?appError?.toString().split('\n').take(4).join(' '),
        ...capture,
      });
    }
    semantics.dispose();
    client.close(force: true);
  }, timeout: Timeout.none);
}

// ---------------------------------------------------------------- transport

Future<Map<String, dynamic>> _get(HttpClient c, String path) async {
  final req = await c.getUrl(Uri.parse('http://127.0.0.1:$_port$path'));
  final res = await req.close().timeout(const Duration(seconds: 40));
  return jsonDecode(await res.transform(utf8.decoder).join()) as Map<String, dynamic>;
}

Future<void> _post(HttpClient c, String path, Map<String, dynamic> body) async {
  final bytes = utf8.encode(jsonEncode(body));
  for (var attempt = 1; ; attempt++) {
    try {
      final req = await c.postUrl(Uri.parse('http://127.0.0.1:$_port$path'));
      req.headers.contentType = ContentType.json;
      req.contentLength = bytes.length; // no chunked encoding
      req.add(bytes);
      await (await req.close()).drain<void>();
      return;
    } catch (e) {
      if (attempt >= 2) {
        debugPrint('QA harness: could not report to $path: $e');
        return; // never crash the test over reporting
      }
      await Future<void>.delayed(const Duration(milliseconds: 500));
    }
  }
}

// ---------------------------------------------------------------- actions

Finder _find(Map<String, dynamic>? t) {
  if (t == null) throw StateError('step has no target');
  Finder f;
  if (t['text'] != null) {
    f = find.text(t['text'] as String, findRichText: true);
  } else if (t['text_contains'] != null) {
    f = find.textContaining(t['text_contains'] as String, findRichText: true);
  } else if (t['key'] != null) {
    f = find.byKey(ValueKey<String>(t['key'] as String));
  } else if (t['semantics'] != null) {
    // exact label: "Waiting" must not hit the "Waiting deposit" chip
    f = find.bySemanticsLabel(RegExp('^${RegExp.escape(t['semantics'] as String)}\$'));
  } else if (t['semantics_contains'] != null) {
    f = find.bySemanticsLabel(RegExp(RegExp.escape(t['semantics_contains'] as String)));
  } else if (t['type'] != null) {
    f = find.byWidgetPredicate((w) => w.runtimeType.toString() == t['type']);
  } else if (t['tooltip'] != null) {
    f = find.byTooltip(t['tooltip'] as String);
  } else if (t['icon'] != null) {
    // "U+EB0B" as listed under no_label_tappables in a capture
    final cp = int.parse((t['icon'] as String).replaceFirst(RegExp('^U\\+'), ''), radix: 16);
    f = find.byWidgetPredicate((w) => w is Icon && w.icon?.codePoint == cp);
  } else {
    throw StateError('unknown target $t');
  }
  if (t['hit_testable'] != false) f = f.hitTestable();
  final count = f.evaluate().length;
  if (count == 0) throw StateError('not found: ${jsonEncode(t)}');
  return f.at((t['index'] as int?) ?? 0);
}

Future<void> _settle(WidgetTester tester, int ms) async {
  for (var waited = 0; waited < ms; waited += 100) {
    await tester.pump(const Duration(milliseconds: 100));
  }
}

/// Runs one step. Returns an optional note; throws when the step fails.
Future<String?> _run(WidgetTester tester, Map<String, dynamic> s) async {
  final settle = (s['settle_ms'] as int?) ?? 1500;
  switch (s['action']) {
    case 'start':
      // Tear down any previous app, reset all state, start again.
      await tester.pumpWidget(const SizedBox.shrink());
      _resetFixtures();
      final mode = s['mode'] as String? ?? 'fresh';
      final options = (s['options'] as Map?)?.cast<String, dynamic>() ?? <String, dynamic>{};
      if (mode == 'fresh') {
        await LocalStorage.initialize();
        await LocalStorage.clear();
        await initializeApp();
        await tester.pumpWidget(const MyApp());
        await _settle(tester, 3000);
      } else if (mode == 'relaunch') {
        // App restart that keeps what is saved on the phone (no storage clear).
        await initializeApp();
        await tester.pumpWidget(const MyApp());
        await _settle(tester, 3000);
      } else if (mode.startsWith('route:')) {
        // Fresh start, then open one route directly (MyApp(initialRoute: ...)).
        await LocalStorage.initialize();
        await LocalStorage.clear();
        await initializeApp();
        await _startRoute(tester, mode.substring(6));
        await _settle(tester, 3000);
      } else {
        await _startMode(tester, mode, options);
      }
      for (final v in (s['variants'] as Map<String, dynamic>? ?? {}).entries) {
        _setVariant(v.key, v.value);
      }
      return null;
    case 'variant':
      _setVariant(s['name'] as String, s['variant']);
      return 'variant set; it applies to the next data load';
    case 'helper':
      // One of the team's own integration-test helpers, e.g. openQuoteRow(tester, 'Maria Santos').
      await _helper(tester, s['name'] as String, (s['args'] as List?) ?? const [],
          (s['options'] as Map?)?.cast<String, dynamic>() ?? <String, dynamic>{});
    case 'tap':
      final t = s['target'] as Map<String, dynamic>?;
      if (t != null && t['point'] is List) {
        final pt = t['point'] as List;
        await tester.tapAt(Offset((pt[0] as num).toDouble(), (pt[1] as num).toDouble()));
      } else {
        await tester.tap(_find(t), warnIfMissed: false);
      }
    case 'long_press':
      await tester.longPress(_find(s['target'] as Map<String, dynamic>?), warnIfMissed: false);
    case 'enter_text':
      await tester.enterText(_find(s['target'] as Map<String, dynamic>?), s['text'] as String);
    case 'scroll':
      final px = ((s['pixels'] as num?) ?? 300).toDouble();
      final offset = switch (s['direction'] as String? ?? 'down') {
        'down' => Offset(0, -px),
        'up' => Offset(0, px),
        'left' => Offset(px, 0),
        'right' => Offset(-px, 0),
        _ => throw StateError('direction must be up/down/left/right'),
      };
      final t = s['target'] as Map<String, dynamic>?;
      final area = t != null ? _find(t) : find.byType(Scrollable).hitTestable().first;
      await tester.drag(area, offset, warnIfMissed: false);
    case 'scroll_to':
      final t = Map<String, dynamic>.from(s['target'] as Map)..['hit_testable'] = false;
      await tester.scrollUntilVisible(_find(t), 200,
          scrollable: find.byType(Scrollable).hitTestable().first, maxScrolls: 30);
    case 'back':
      await tester.binding.handlePopRoute();
    case 'wait':
      await _settle(tester, (s['ms'] as int?) ?? 1000);
      return null;
    case 'capture':
      return null;
    default:
      throw StateError('unknown action ${s['action']}');
  }
  await _settle(tester, settle);
  return null;
}

// ---------------------------------------------------------------- capture

// Private Use Area: where icon fonts (Tabler, Material Symbols...) put glyphs.
final _iconGlyphs = RegExp('[\uE000-\uF8FF]');
final _iconFont = RegExp('icon|symbol|awesome|tabler', caseSensitive: false);

bool _hidden(RenderObject r) {
  for (RenderObject? p = r; p != null; p = p.parent) {
    if (p is RenderOffstage && p.offstage) return true;
    if (p is RenderOpacity && p.opacity == 0) return true;
    if (p is RenderAnimatedOpacity && p.opacity.value == 0) return true;
  }
  return false;
}

/// True when [r] (or something inside it) is what a tap at [p] would hit,
/// i.e. it is not covered by a later route, sheet or dialog.
bool _onTop(WidgetTester tester, RenderObject r, Offset p) {
  final result = tester.hitTestOnBinding(p);
  for (final e in result.path) {
    for (Object? t = e.target; t is RenderObject; t = t.parent) {
      if (identical(t, r)) return true;
    }
  }
  return false;
}

Rect? _onScreen(RenderBox box, Size screen) {
  if (!box.attached || !box.hasSize || box.size.isEmpty) return null;
  final topLeft = box.localToGlobal(Offset.zero);
  final rect = topLeft & box.size;
  final view = Offset.zero & screen;
  return rect.overlaps(view) ? rect : null;
}

Map<String, dynamic> _capture(WidgetTester tester) {
  final screen = tester.view.physicalSize / tester.view.devicePixelRatio;
  final texts = <(double, double, String)>[];
  var covered = 0; // text drawn but hidden under a later route / sheet
  void walk(RenderObject r) {
    if (r is RenderOffstage && r.offstage) return;
    if (r is RenderOpacity && r.opacity == 0) return;
    // Icon widgets draw a glyph in an icon font (some fonts map glyphs onto
    // ordinary letters like U+6B04), so skip by font, not by character.
    if (r is RenderParagraph && _iconFont.hasMatch(r.text.style?.fontFamily ?? '')) {
      return;
    }
    if (r is RenderParagraph || r is RenderEditable) {
      final rect = _onScreen(r as RenderBox, screen);
      var t = r is RenderParagraph
          ? r.text.toPlainText()
          : ((r as RenderEditable).text?.toPlainText() ?? '');
      t = t.replaceAll(_iconGlyphs, '').trim(); // icon-font glyphs say nothing
      if (rect != null && t.isNotEmpty) {
        final p = Offset(rect.center.dx.clamp(1, screen.width - 1), rect.center.dy.clamp(1, screen.height - 1));
        if (_onTop(tester, r, p)) {
          final cut = r is RenderParagraph && r.didExceedMaxLines ? ' [cut off]' : '';
          texts.add((rect.top, rect.left, r is RenderEditable ? '[field] $t' : '$t$cut'));
        } else {
          covered++;
        }
      }
    }
    r.visitChildren(walk);
  }
  for (final view in tester.binding.renderViews) {
    walk(view);
  }
  texts.sort((a, b) => (a.$1 - b.$1).abs() < 4 ? a.$2.compareTo(b.$2) : a.$1.compareTo(b.$1));

  // Accessibility tree: buttons, fields, focus, checked/selected states.
  final nodes = <String>[];
  String? focused;
  void sem(SemanticsNode node) {
    if (!node.isInvisible) {
      final d = node.getSemanticsData();
      final f = d.flagsCollection;
      final tags = <String>[
        if (f.isButton) 'button',
        if (f.isTextField) 'field',
        if (f.isHeader) 'header',
        if (f.isFocused == Tristate.isTrue) 'focused',
        if (f.isChecked == CheckedState.isTrue) 'checked',
        if (f.isToggled == Tristate.isTrue) 'on',
        if (f.isToggled == Tristate.isFalse) 'off',
        if (f.isSelected == Tristate.isTrue) 'selected',
        if (f.isEnabled == Tristate.isFalse) 'disabled',
        if (!f.isButton && !f.isTextField && d.hasAction(SemanticsAction.tap)) 'tap',
      ];
      final words = [
        if (d.label.isNotEmpty) d.label,
        if (d.value.isNotEmpty) 'value: ${d.value}',
        if (d.hint.isNotEmpty) 'hint: ${d.hint}',
      ].join(' | ');
      if (words.isNotEmpty || tags.contains('field')) {
        final line = (tags.isEmpty ? words : '[${tags.join(' ')}] $words')
            .replaceAll(_iconGlyphs, '')
            .trim();
        nodes.add(line.replaceAll('\n', ' '));
        if (f.isFocused == Tristate.isTrue) focused = line;
      }
    }
    node.visitChildren((c) {
      sem(c);
      return true;
    });
  }
  for (final view in tester.binding.renderViews) {
    final root = view.owner?.semanticsOwner?.rootSemanticsNode;
    if (root != null) sem(root);
  }

  // Which screens are showing (Max names screen widgets *Screen).
  final screens = <String>[];
  for (final e in tester.allElements) {
    final name = e.widget.runtimeType.toString();
    if (!name.endsWith('Screen') || screens.contains(name)) continue;
    final r = e.renderObject;
    final rect = r is RenderBox && !_hidden(r) ? _onScreen(r, screen) : null;
    if (rect != null && _onTop(tester, r!, rect.center)) screens.add(name);
  }

  // Tappable things with no words (icon-only buttons): a driver can tap them
  // by icon or point, and they are an accessibility finding in themselves.
  final noLabel = <Map<String, dynamic>>[];
  // Tappables whose tap area is under 48x48 logical px (Material / WCAG target size).
  final small = <Map<String, dynamic>>[];
  for (final e in tester.allElements) {
    final w = e.widget;
    final tappable = (w is GestureDetector && w.onTap != null) ||
        (w is InkResponse && w.onTap != null) ||
        (w is IconButton && w.onPressed != null);
    final r = e.renderObject;
    if (!tappable || r is! RenderBox || _hidden(r)) continue;
    final rect = _onScreen(r, screen);
    if (rect == null || rect.isEmpty) continue;
    if (!_onTop(tester, r, rect.center)) continue; // covered by a later screen
    var hasWords = false;
    String? words;
    final icons = <String>{};
    void look(Element c) {
      final cw = c.widget;
      if (cw is Text || cw is RichText || cw is EditableText) {
        final ro = c.renderObject;
        if (!(ro is RenderParagraph && _iconFont.hasMatch(ro.text.style?.fontFamily ?? ''))) {
          hasWords = true;
          if (ro is RenderParagraph) words ??= ro.text.toPlainText().replaceAll(_iconGlyphs, '').trim();
        }
      }
      if (cw is Semantics && (cw.properties.label ?? '').isNotEmpty) {
        hasWords = true;
        words ??= cw.properties.label;
      }
      if (cw is Icon && cw.icon != null) {
        icons.add('U+${cw.icon!.codePoint.toRadixString(16).toUpperCase()}');
      }
      if (!hasWords) c.visitChildren(look);
    }
    e.visitChildren(look);
    // Material buttons take taps on an invisible padding around them
    // (_RenderInputPadding): that padded box is the real tap area.
    var area = rect;
    RenderObject? up = r.parent;
    for (var k = 0; k < 6 && up != null; k++, up = up.parent) {
      if (up.runtimeType.toString() == '_RenderInputPadding' && up is RenderBox) {
        area = _onScreen(up, screen) ?? rect;
        break;
      }
    }
    final c = rect.center;
    final where = '${c.dy < screen.height / 3 ? 'top' : c.dy > screen.height * 2 / 3 ? 'bottom' : 'middle'} '
        '${c.dx < screen.width / 3 ? 'left' : c.dx > screen.width * 2 / 3 ? 'right' : 'centre'}';
    if ((area.width < 47.5 || area.height < 47.5) && small.length < 20 &&
        !small.any((x) => x['point'].toString() == [c.dx.round(), c.dy.round()].toString())) {
      small.add({'label': (words?.isNotEmpty ?? false) ? words : (icons.isEmpty ? '(none)' : 'icon ${icons.first}'),
                 'where': where, 'point': [c.dx.round(), c.dy.round()],
                 'tap_area': '${area.width.round()}x${area.height.round()}'});
    }
    if (rect.width < 12 || rect.height < 12) continue;
    if (hasWords || icons.isEmpty) continue;
    final entry = {'icon': icons.first, 'where': where,
                   'point': [c.dx.round(), c.dy.round()],
                   'size': '${rect.width.round()}x${rect.height.round()}'};
    if (!noLabel.any((x) => x['icon'] == entry['icon'] && x['point'].toString() == entry['point'].toString())) {
      noLabel.add(entry);
    }
  }

  final insets = tester.view.viewInsets.bottom / tester.view.devicePixelRatio;
  // On the simulator the on-screen keyboard may stay hidden (hardware keyboard),
  // so "a text field has focus" is the reliable sign the app asked for it.
  final editing = FocusManager.instance.primaryFocus?.context
          ?.findAncestorWidgetOfExactType<EditableText>() !=
      null;
  final now = DateTime.now();
  return {
    'local_time': '${now.hour.toString().padLeft(2, '0')}:${now.minute.toString().padLeft(2, '0')}',
    'keyboard_requested': editing || (focused?.contains('field') ?? false),
    'screens': screens,
    'texts': [for (final t in texts) t.$3],
    'covered_text_count': covered,
    'semantics': nodes.take(200).toList(),
    'no_label_tappables': noLabel.take(20).toList(),
    'small_tappables': small,
    'focused': focused,
    'keyboard_inset': insets.round(),
  };
}

// ---------------------------------------------------------------- values from steps
// Step values arrive as JSON (bool, number, list) or as text from step.py.
// Not every app needs every converter, so unused ones are fine here.
// ignore_for_file: unused_element

bool _qaBool(Object? v) => v is bool ? v : v.toString().trim().toLowerCase() == 'true';
int _qaInt(Object? v) => v is num ? v.toInt() : int.parse(v.toString().trim());
double _qaDouble(Object? v) => v is num ? v.toDouble() : double.parse(v.toString().trim());
String _qaStr(Object? v) => v.toString();
List<String> _qaStrings(Object? v) => v is List
    ? [for (final x in v) x.toString()]
    : v.toString().split(',').map((x) => x.trim()).where((x) => x.isNotEmpty).toList();
// "300" or "300ms" -> milliseconds, "2s" -> seconds
Duration _qaDuration(Object? v) {
  final t = v.toString().trim().toLowerCase();
  if (t.endsWith('ms')) return Duration(milliseconds: int.parse(t.substring(0, t.length - 2)));
  if (t.endsWith('s')) return Duration(milliseconds: (double.parse(t.substring(0, t.length - 1)) * 1000).round());
  return Duration(milliseconds: _qaInt(t));
}
// "phone_supported=false,internet_connected=false"
Map<String, bool> _qaMapBool(Object? v) => v is Map
    ? {for (final e in v.entries) e.key.toString(): _qaBool(e.value)}
    : {
        for (final kv in v.toString().split(',').where((x) => x.contains('=')))
          kv.split('=')[0].trim(): _qaBool(kv.split('=')[1]),
      };
bool _qaNull(Object? v) => v == null || v.toString().trim() == 'null';
Object? _qaArg(List<dynamic> a, int i, String helper, String what) {
  if (i >= a.length) throw StateError('helper $helper needs argument ${i + 1}: $what');
  return a[i];
}
Object? _qaOpt(Map<String, dynamic> o, String name, String where, String what) {
  if (!o.containsKey(name)) throw StateError('$where needs option $name ($what)');
  return o[name];
}

// ---------------------------------------------------------------- generated for this app
// @@GENERATED@@
