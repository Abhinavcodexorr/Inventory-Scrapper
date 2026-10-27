"""Guards the tests/*.py convention this project relies on: every module keeps
a hand-written main() so it stays runnable via `python -m tests.test_x`
(no pytest infrastructure required), and every `def test_*` function in that
module must be called from its own main() — otherwise adding a test function
and forgetting to add it to main() means it silently never runs under that
invocation, which is exactly the failure mode this test exists to catch.

Modules with no test_* functions at all (e.g. ones whose assertions live
directly in main()) are exempt — there's nothing to forget to wire up.

Run:  pytest -q tests/test_runner_completeness.py
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).parent
_EXCLUDE = {"__init__.py", "conftest.py", "test_runner_completeness.py"}


def _test_modules() -> list[Path]:
    return sorted(p for p in TESTS_DIR.glob("test_*.py") if p.name not in _EXCLUDE)


def _analyze(path: Path) -> tuple[set[str], set[str], bool]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    test_funcs: set[str] = set()
    main_refs: set[str] = set()
    has_main = False
    for node in tree.body:
        if not isinstance(node, ast.FunctionDef):
            continue
        if node.name.startswith("test_"):
            test_funcs.add(node.name)
        elif node.name == "main":
            has_main = True
            # Any reference to the name counts, not just a direct call — some
            # modules (e.g. test_sync_integration.py) iterate `for fn in
            # (test_a, test_b, ...): fn()` rather than calling each by name
            # directly. What matters is that the name is reachable from
            # main(), by whatever means.
            for sub in ast.walk(node):
                if isinstance(sub, ast.Name) and isinstance(sub.ctx, ast.Load):
                    main_refs.add(sub.id)
    return test_funcs, main_refs, has_main


@pytest.mark.parametrize("path", _test_modules(), ids=lambda p: p.name)
def test_every_test_function_is_called_from_main(path: Path) -> None:
    test_funcs, main_calls, has_main = _analyze(path)
    if not test_funcs:
        pytest.skip(f"{path.name} defines no test_* functions")
    if not has_main:
        pytest.fail(f"{path.name} defines test_* function(s) but no main() to run them under `python -m`")
    missing = test_funcs - main_calls
    assert not missing, (
        f"{path.name}: test function(s) {sorted(missing)} are never called from main() — "
        f"they silently never run under `python -m tests.{path.stem}`"
    )


def main() -> None:
    for path in _test_modules():
        test_every_test_function_is_called_from_main(path)
    print("TEST RUNNER COMPLETENESS TEST PASSED")


if __name__ == "__main__":
    main()
