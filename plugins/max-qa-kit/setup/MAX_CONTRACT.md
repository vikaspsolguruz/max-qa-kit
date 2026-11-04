# Max Architecture contract for the QA kit

## In short

What the AI QA kit relies on in **every** Max Architecture app (from the SG CLI
`sg init` boilerplate) and in our SDD specs. Everything else — fake-data switches,
test helpers, test data, routes, permissions — is discovered per app by `setup`.
Generated from `max_contract.json` by `check_max.py --write-md`; check any app with
`python3 -I kit/setup/check_max.py <app>`.

| Item | Need | Used for | Boilerplate source | What is checked in an app |
|---|---|---|---|---|
| max_marker | required | recognise a Max app | `sg_cli.yaml:1` `version: max` | `sg_cli.yaml` matches `^version:\s*max\b` |
| package_name | required | imports in the generated harness (package:<name>/...) | `pubspec.yaml:1` `name: max_arch` | `pubspec.yaml` matches `^name:\s*(\w+)` |
| integration_test_dep | required | the harness runs as an integration test | `pubspec.yaml:75` `  integration_test:` | `pubspec.yaml` matches `^\s+integration_test:` |
| main_init_then_app | required | harness starts the app the same way main() does | `lib/main.dart:6` `await initializeApp();` | `lib/main.dart` matches `await initializeApp\(\)` |
| initialize_app | required | fresh / relaunch starts | `lib/_app_initializer.dart:13` `Future<void> initializeApp() async {` | `lib/_app_initializer.dart` matches `Future<void>\s+initializeApp\(\)` |
| my_app | required | the root widget the harness pumps | `lib/app/app.dart:11` `class MyApp extends StatelessWidget {` | `lib/app/app.dart` matches `class MyApp\b` |
| my_app_initial_route | optional | start directly on a route (apps without test helpers) | `lib/app/app.dart:12` `const MyApp({super.key, this.initialRoute});` | `lib/app/app.dart` matches `this\.initialRoute` |
| route_names | optional | list of route starts | `lib/app/app_routes/_route_names.dart:6` `static const String home = '/home';` | `lib/app/app_routes/_route_names.dart` matches `static const String \w+ = '` |
| local_storage_initialize | required | fresh start (storage ready) | `lib/core/local_storage/local_storage.dart:17` `static Future<void> initialize() async => await _prefManager.initialize();` | `lib/core/local_storage/local_storage.dart` matches `static Future<void> initialize\(` |
| local_storage_clear | required | fresh start (nothing saved) | `lib/core/local_storage/local_storage.dart:19` `static Future<void> clear() async => _prefManager.clear();` | `lib/core/local_storage/local_storage.dart` matches `static Future<void> clear\(` |
| raw_response_fake | optional | fake data in repositories; backend switch | `lib/core/models/response/raw_response_model.dart:29` `RawResponse.fake(Map<String, dynamic> fakeData)` | `lib/core/models/response/raw_response_model.dart` matches `RawResponse\.fake\(` |
| fake_models_dir | optional | fake-data switches the harness can flip | `lib/core/fake_models/fake_user_model.dart:1` | folder `lib/core/fake_models/` |
| patrol_dep | optional | Patrol tests (team's own flow tests) | `pubspec.yaml:78` `  patrol: ^4.4.0` | `pubspec.yaml` matches `^\s+patrol:` |
| sdd_specs | optional | coverage map and /qa plans (our SDD) | .specify/templates/spec-template.md:29 — '1. **US1-AS1**: **Given** [initial state], **When** [action], **Then** [expected outcome]' | folder `specs/` |

## Discovered per app (not part of the contract)

- fake-data switches in `lib/core/fake_models/` (enum fields, bool maps, `reset()`)
- start helpers in `integration_test/**` (`seedX()`, `driveToX(tester)`; Patrol-only ones are listed, not called)
- route names in `_route_names.dart` (top-level `/x` routes vs nested `parent/child` routes)
- test data per fixture file; iOS permissions from `ios/Runner/Info.plist`; bundle id; simulator
