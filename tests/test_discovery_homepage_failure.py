"""A homepage that will not open says why, and is not sold as custom work.

Every failure used to read "needs a custom scraper build", including a mistyped
domain and universalauto.ca's mismatched certificate.

Run:  python -m tests.test_discovery_homepage_failure
"""
from __future__ import annotations

from dealer_scraper.discovery import coordinator
from dealer_scraper.discovery.coordinator import _homepage_failure, _probe_after_redirects


def test_unknown_domain_is_not_custom_work() -> None:
    summary, custom = _homepage_failure(Exception("Page.goto: net::ERR_NAME_NOT_RESOLVED at https://x.ca/"))
    assert "could not be found" in summary and custom is False


def test_bad_certificate_is_not_custom_work() -> None:
    summary, custom = _homepage_failure(Exception("Page.goto: net::ERR_CERT_COMMON_NAME_INVALID at https://x.ca/"))
    assert "certificate" in summary and custom is False


def test_unresponsive_site_is_not_custom_work() -> None:
    for text in ("Page.goto: net::ERR_CONNECTION_REFUSED at https://x.ca/",
                 "Page.goto: Timeout 45000ms exceeded."):
        summary, custom = _homepage_failure(Exception(text))
        assert "did not respond" in summary and custom is False


def test_unrecognised_failure_keeps_the_custom_work_message() -> None:
    summary, custom = _homepage_failure(Exception("Page.goto: net::ERR_HTTP2_PROTOCOL_ERROR at https://x.ca/"))
    assert "custom scraper build" in summary and custom is True


class _Heuristics:
    detail_segments = ("cars",)


class _RedirectingPage:
    """Destroys the probe's context `failures` times, then answers."""

    def __init__(self, failures: int, message: str = "Execution context was destroyed") -> None:
        self.failures = failures
        self.message = message
        self.calls = 0

    def evaluate(self, _js, _arg):
        self.calls += 1
        if self.calls <= self.failures:
            raise RuntimeError(self.message)
        return {"ok": True}


def _without_waiting(run) -> None:
    original = coordinator.wait_for_stable
    coordinator.wait_for_stable = lambda _page, _h: None
    try:
        run()
    finally:
        coordinator.wait_for_stable = original


def test_script_redirect_during_the_probe_is_retried() -> None:
    def run() -> None:
        page = _RedirectingPage(failures=1)
        assert _probe_after_redirects(page, _Heuristics()) == {"ok": True}
        assert page.calls == 2
    _without_waiting(run)


def test_other_probe_errors_are_not_retried() -> None:
    def run() -> None:
        page = _RedirectingPage(failures=1, message="some other failure")
        try:
            _probe_after_redirects(page, _Heuristics())
        except RuntimeError:
            assert page.calls == 1
            return
        raise AssertionError("expected the error to propagate")
    _without_waiting(run)


def test_repeated_redirects_eventually_give_up() -> None:
    def run() -> None:
        page = _RedirectingPage(failures=5)
        try:
            _probe_after_redirects(page, _Heuristics())
        except RuntimeError:
            assert page.calls == 3
            return
        raise AssertionError("expected the error to propagate")
    _without_waiting(run)


def main() -> None:
    test_unknown_domain_is_not_custom_work()
    test_bad_certificate_is_not_custom_work()
    test_unresponsive_site_is_not_custom_work()
    test_unrecognised_failure_keeps_the_custom_work_message()
    test_script_redirect_during_the_probe_is_retried()
    test_other_probe_errors_are_not_retried()
    test_repeated_redirects_eventually_give_up()
    print("homepage failure tests passed")


if __name__ == "__main__":
    main()
