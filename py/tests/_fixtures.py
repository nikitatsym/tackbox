"""Shared inline test fixtures. Kept out of conftest so they import cleanly
as data (`from _fixtures import ...`) without pulling in pytest plumbing."""

# One violation per migrated python rule (ERC/TBX ports): swallow, bare except,
# reraise without cause, useless catch, exit-in-except, contextlib.suppress,
# import-inside. Consumed by the pyrules unit tests and the CLI dispatch test.
PY_ONE_PER_RULE = """import sys
import contextlib


def swallowed():
    try:
        work()
    except ValueError as e:
        pass


def bare():
    try:
        work()
    except:
        pass


def reraise_no_cause():
    try:
        work()
    except ValueError as e:
        raise RuntimeError("wrapped")


def useless():
    try:
        work()
    except ValueError:
        raise


def exit_in_except():
    try:
        work()
    except ValueError:
        sys.exit(1)


def suppressed():
    with contextlib.suppress(Exception):
        work()


def import_inside():
    import json
    return json
"""
