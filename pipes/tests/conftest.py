"""A test that reaches a scan-route step needing a missing translator module (pipes.reuse.TranslatorMissing: the
translator's preprocessing / OCR, on branch merge-v03) is reported as skipped with that reason, not as a failure."""

import pytest

from pipes.reuse import TranslatorMissing


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()
    if call.excinfo is not None and call.excinfo.errisinstance(TranslatorMissing):
        report.outcome = "skipped"
        report.longrepr = (str(item.path), item.location[1] or 0, f"Skipped: {call.excinfo.value}")
