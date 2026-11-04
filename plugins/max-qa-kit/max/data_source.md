# Fake or real backend, per repository method (Max Architecture)

## In short

The same app and the same flow tests run on **fake data** (every change, fast,
never flaky) or on the **real backend** (nightly / before a release), chosen at
build time with one flag. Fake stays the default.

```bash
flutter test integration_test/<file> -d <sim>                                   # fake (default)
flutter test integration_test/<file> -d <sim> --dart-define=APP_FLAVOR=stage \
                                              --dart-define=DATA_SOURCE=api       # real staging API
```

## Why per method, not per repository

A repository can already mix both: pencildrop's `auth_repository.dart` calls
`ApiClient.instance.post(...)` for the template's email/Google/Apple methods
while phone login and the device check are fake (`fake_auth_fixtures.dart`).
So the choice is made **per method**. A method with no real endpoint yet stays
on fake even in api mode, and is easy to list.

## The pattern

`lib/core/data_source/data_source.dart` (one file, read once at build time):

```dart
enum DataSource { fake, api }

const _dataSourceName = String.fromEnvironment('DATA_SOURCE', defaultValue: 'fake');
const DataSource dataSource = _dataSourceName == 'api' ? DataSource.api : DataSource.fake;

/// Real API when DATA_SOURCE=api and the method has one; fake otherwise.
/// `api: null` marks a method with no endpoint yet (it stays on fake).
Future<T> fromSource<T>({required Future<T> Function() fake, Future<T> Function()? api}) =>
    dataSource == DataSource.api && api != null ? api() : fake();
```

A repository method keeps its fake body untouched and gains an `api` branch
written exactly like `docs/repositories.md` (`ApiClient.instance.get/post/...`
with `ApiLinks`):

```dart
static Future<ListResponse<QuoteSummaryModel>> fetchAllQuotes() => fromSource(
      fake: _fakeAllQuotes,
      api: () async {
        final apiData = await ApiClient.instance.get(ApiLinks.quotes);
        return apiData.getListResponse<QuoteSummaryModel>(
          dataParser: (data) => (data['data'] as List).map((e) => QuoteSummaryModel.fromJson(e)).toList(),
        );
      },
    );
```

The response types are the same in both branches (`NormalResponse` /
`ListResponse`), so screens, BLoCs and tests do not change.

## Rules

- Fake is the default. CI runs fake on every merge request.
- `ApiLinks` paths must match the real API; the prototype's `/quotes` is a
  placeholder until the backend team confirms it.
- Tests that set fixture variants (`FakeQuotesFixtures.variant = ...error`)
  only make sense on fake. Run them in fake mode; in api mode run the tests
  that do not depend on variants, against staging test accounts.
- Real-backend runs need from the project: staging base URL per `APP_FLAVOR`,
  test accounts, and a way past OTP (fixed test code or test numbers).
- List of methods still fake-only: `grep -rn "api: null" lib/core/repositories`
  plus any method not yet wrapped in `fromSource`.

## Proven on pencildrop (temporarily applied, then removed)

See `results/backend_switch/` for the patch and the runs: fake mode still
passes the quotes tests; api mode sends the quotes request to the configured
base URL (no backend there, so the screen shows its normal error state).
