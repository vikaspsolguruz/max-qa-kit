"""Single source of truth for label definitions, Jev questions and rules.

Claude's prompt is built from the same LABEL_CRITERIA, so both contestants
are judged against identical definitions.
"""

LABEL_CRITERIA = {
    "real_bug": "The code change in `diff` broke app behavior that the failing "
                "test correctly checks, and `commit_message` does not ask for "
                "that new behavior.",
    "outdated_test": "The code change in `diff` deliberately changed app "
                     "behavior as `commit_message` describes, and the unchanged "
                     "test still expects the old behavior.",
    "broken_test": "The developer added or edited a test in `diff`, and the "
                   "mistake is in that test (wrong expected value, missing mock "
                   "setup, missing app wrapper), not in the app code.",
    "compile_error": "App or test code changed in `diff` does not compile (for "
                     "example a renamed function, a wrong type, or a removed "
                     "import), with no package version change.",
    "dependency_break": "A package version change in `pubspec.yaml` or "
                        "`pubspec.lock` broke version solving or compilation.",
    "environment": "The CI setup failed (missing secret or file, unreachable "
                   "package server, wrong Flutter SDK, missing tool), and the "
                   "code change did not cause it.",
    "flaky": "A test failed from timing, network, or state left over by another "
             "test, unrelated to the change in `diff`.",
    "rendering_drift": "Only the Flutter SDK version changed, and golden images "
                       "differ slightly because of new rendering, with no "
                       "intended UI change.",
}

YES, NO = 0.7, 0.3

QUESTIONS = {
    # A) one holistic choice
    "cause": {
        "type": "choice",
        "instructions": "What caused the CI failure in `log`? If several tests "
                        "fail, answer for the failure the change in `diff` "
                        "caused.",
        "criteria": LABEL_CRITERIA,
    },
    # B) atomic judgments; code combines them in decide()
    "setup_failed": {
        "type": "noul",
        "instructions": "Did the CI job in `log` fail while preparing the "
                        "project, before any test could run?",
        "criteria": {
            "true": "A secret file, the package download, the SDK check, an "
                    "asset or a missing tool failed.",
            "false": "Tests ran and at least one test failed.",
        },
    },
    "timing_or_network": {
        "type": "noul",
        "instructions": "Did a failing test in `log` stop because it ran out of "
                        "time or could not reach a network address?",
        "criteria": {
            "true": "A timeout, a pumpAndSettle timeout, or a host lookup or "
                    "socket error stopped the test.",
            "false": "The test ran to an assertion that compared a result with "
                     "an expected value.",
        },
    },
    "leftover_state": {
        "type": "noul",
        "instructions": "Did a failing test in `log` find data that another test "
                        "had left behind?",
        "criteria": {
            "true": "The test expected a fresh, empty state but found values "
                    "another test added earlier in the run.",
            "false": "The value came from the code under test itself.",
        },
    },
    "diff_touches_failure": {
        "type": "noul",
        "instructions": "Does `diff` change the code, text or test that a "
                        "failing test in `log` checks?",
        "criteria": {
            "true": "The changed lines produce the value or screen the test "
                    "compares, or change the failing test itself.",
            "false": "The diff only edits unrelated files, documentation, or "
                     "formatting and comments.",
        },
    },
    "test_is_wrong": {
        "type": "noul",
        "instructions": "Is the mistake in the test code shown in `source`, "
                        "not in the app code?",
        "criteria": {
            "true": "The test expects a value that contradicts its own name or "
                    "the app's behavior, or it is missing setup such as a mock "
                    "stub or a MaterialApp wrapper.",
            "false": "The test is correct, and the app code returns a different "
                     "result.",
        },
    },
    "result_matches_intent": {
        "type": "noul",
        "instructions": "Is the new behavior that makes the test fail what "
                        "`commit_message` says the change should do?",
        "criteria": {
            "true": "The commit asks for this new behavior, for example a "
                    "feature that changes the output the test sees.",
            "false": "The commit promises no behavior change (refactor, "
                     "cleanup, docs) or a different result.",
        },
    },
    "only_versions_changed": {
        "type": "noul",
        "instructions": "Apart from formatting or comment edits, does `diff` "
                        "change only Flutter, Dart or package versions?",
    },
}


def decide(n, f):
    """Truth table. n = Noul probabilities, f = code facts.
    Returns (label, reason)."""
    if f["version_solving_failed"]:
        return (("dependency_break", "solving failed, deps changed")
                if f["dependency_files_changed"]
                else ("environment", "solving failed, deps unchanged"))
    if f["compile_errors_in_log"]:
        return (("dependency_break", "compile errors, deps changed")
                if f["dependency_files_changed"]
                else ("compile_error", "compile errors"))
    if not f["tests_started"] or n["setup_failed"] >= YES:
        return "environment", "no tests ran / setup failed"
    if f["golden_image_failures"] and f["flutter_sdk_version_changed"] \
            and n["only_versions_changed"] >= YES:
        return "rendering_drift", "golden diff, only SDK changed"

    flaky_sign = max(n["timing_or_network"], n["leftover_state"])
    if n["diff_touches_failure"] <= NO:
        return (("flaky", "unrelated diff, timing/state") if flaky_sign >= YES
                else ("needs_human", "unrelated diff, cause unclear"))
    if n["diff_touches_failure"] < YES:
        return (("flaky", "timing/state") if flaky_sign >= YES
                else ("needs_human", "relevance unsettled"))

    if f["test_files_changed"]:
        if n["test_is_wrong"] >= YES:
            return "broken_test", "edited test is wrong"
        if n["test_is_wrong"] > NO:
            return "needs_human", "test vs code unsettled"
    if n["result_matches_intent"] >= YES:
        return "outdated_test", "new behavior intended"
    if n["result_matches_intent"] <= NO:
        return "real_bug", "new behavior not intended"
    return "needs_human", "intent unsettled"


CLAUDE_SYSTEM = (
    "You triage failed Flutter CI runs. Classify what caused the failure "
    "into exactly one label.\n\nLabels:\n" +
    "\n".join(f"- {k}: {v}" for k, v in LABEL_CRITERIA.items()) +
    "\n\nIf several tests fail, answer for the failure the change in `diff` "
    "caused.\n\nThe input is JSON with: commit_message, diff, log, facts "
    "(exact values computed by code), and source (code around the failure "
    "locations, as it looks after the change).\n\nReply with only a JSON "
    'object: {"label": "<one label>", "confidence": <0 to 1>, '
    '"reason": "<one sentence>"}'
)
