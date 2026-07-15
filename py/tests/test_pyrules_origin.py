"""Adversarial coverage for D010: tier-1 recognition by import origin.

Each fixture is an inline string written to a temp dir and linted through the
same closed `flake8 --isolated --disable-noqa --select=TBX [--reporters=...]`
form the engine uses. The focus is the origin resolver's kill/shadow semantics
and the owner/test/tier-2 interplay, not the swallow analysis itself (that lives
in test_pyrules.py).
"""

from __future__ import annotations

from test_pyrules import _flake8


def _write(tmp_path, name, text):
    # Nested-path variant of test_pyrules._write: some fixtures need a package
    # dir (tackbox_report/, go testdata/ shapes) created first.
    p = tmp_path / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    return name


# 1. ADVERSARIAL shadow attack: a later module-level def rebinds the imported
#    name, so the call in the handler is no longer the verb -> the silent catch
#    fires. The shadow attack self-defeats (D010).
def test_shadow_rebinding_kills_credit(tmp_path):
    _write(
        tmp_path, "s.py",
        "from tackbox_report import report_error\n\n\n"
        "def report_error(x):\n    pass\n\n\n"
        "def h():\n    try:\n        work()\n"
        "    except ValueError as e:\n        report_error(e)\n",
    )
    r = _flake8(tmp_path, "s.py")
    assert r.returncode == 1 and "TBX001" in r.stdout, r.stdout


# 2. ADVERSARIAL fake notify: no import, a local def notify - never the verb, so
#    the narrow-catch notify neither credits (TBX001 fires) nor gates (no TBX010).
def test_local_notify_def_is_not_the_verb(tmp_path):
    _write(
        tmp_path, "n.py",
        "def notify(x):\n    print(x)\n\n\n"
        "def h():\n    try:\n        work()\n"
        "    except ValueError as e:\n        notify(e)\n        return\n",
    )
    r = _flake8(tmp_path, "n.py")
    assert r.returncode == 1 and "TBX001" in r.stdout, r.stdout
    assert "TBX010" not in r.stdout, r.stdout


# 3. Attribute credit through `import tackbox_report [as rep]` (latent-gap
#    regression): the attribute-form call resolves, so no swallow.
def test_attribute_credit_aliased(tmp_path):
    _write(
        tmp_path, "a.py",
        "import tackbox_report as rep\n\n\n"
        "def h():\n    try:\n        work()\n"
        "    except ValueError as e:\n"
        "        rep.report_error('m', e, dedup_key='a.b')\n",
    )
    r = _flake8(tmp_path, "a.py")
    assert r.returncode == 0, f"{r.stdout}\n{r.stderr}"


def test_attribute_credit_unaliased(tmp_path):
    _write(
        tmp_path, "a.py",
        "import tackbox_report\n\n\n"
        "def h():\n    try:\n        work()\n"
        "    except ValueError as e:\n"
        "        tackbox_report.report_error('m', e, dedup_key='a.b')\n",
    )
    r = _flake8(tmp_path, "a.py")
    assert r.returncode == 0, f"{r.stdout}\n{r.stderr}"


# 4. TBX011 fires on attribute-style calls (new capability under origin).
def test_tbx011_on_attribute_notify_dynamic_msg(tmp_path):
    _write(
        tmp_path, "a.py",
        "import tackbox_report as rep\n\n\n"
        "def h(msg_var):\n    try:\n        work()\n"
        "    except ValueError as e:\n"
        "        rep.notify(msg_var, e, dedup_key='a.b')\n",
    )
    r = _flake8(tmp_path, "a.py")
    assert r.returncode == 1 and "TBX011" in r.stdout, r.stdout


def test_tbx011_on_attribute_report_error_missing_dedup(tmp_path):
    _write(
        tmp_path, "a.py",
        "import tackbox_report as rep\n\n\n"
        "def h():\n    try:\n        work()\n"
        "    except ValueError as e:\n"
        "        rep.report_error('m', e)\n",
    )
    r = _flake8(tmp_path, "a.py")
    assert r.returncode == 1 and "TBX011" in r.stdout, r.stdout


# 5. A module-level call BEFORE the import resolves against the pre-import state:
#    no credit, so the module-level handler swallows.
def test_module_level_call_before_import_swallows(tmp_path):
    _write(
        tmp_path, "m.py",
        "try:\n    work()\n"
        "except ValueError as e:\n"
        "    report_error('m', e, dedup_key='a.b')\n"
        "from tackbox_report import report_error\n",
    )
    r = _flake8(tmp_path, "m.py")
    assert r.returncode == 1 and "TBX001" in r.stdout, r.stdout


# 6. `del name` after the import kills the binding from that point.
def test_del_kills_binding(tmp_path):
    _write(
        tmp_path, "d.py",
        "from tackbox_report import report_error\n"
        "del report_error\n\n\n"
        "def h():\n    try:\n        work()\n"
        "    except ValueError as e:\n        report_error(e)\n",
    )
    r = _flake8(tmp_path, "d.py")
    assert r.returncode == 1 and "TBX001" in r.stdout, r.stdout


# 7. A def in a try/except ImportError fallback still kills (later in source
#    order): over-flag, never hide (D010).
def test_import_fallback_def_kills(tmp_path):
    _write(
        tmp_path, "f.py",
        "try:\n    from tackbox_report import report_error\n"
        "except ImportError:\n    def report_error(*a, **k):\n        pass\n\n\n"
        "def h():\n    try:\n        work()\n"
        "    except ValueError as e:\n        report_error(e)\n",
    )
    r = _flake8(tmp_path, "f.py")
    assert r.returncode == 1 and "TBX001" in r.stdout, r.stdout


# 8. Star import from tackbox_report binds the five verbs; from any other module
#    binds nothing.
def test_star_from_tackbox_report_binds(tmp_path):
    _write(
        tmp_path, "s.py",
        "from tackbox_report import *\n\n\n"
        "def h():\n    try:\n        work()\n"
        "    except ConnectionError as e:\n"
        "        notify('offline, retrying', cause=e, dedup_key='net.offline')\n",
    )
    r = _flake8(tmp_path, "s.py")
    assert r.returncode == 0, f"{r.stdout}\n{r.stderr}"


def test_star_from_other_module_binds_nothing(tmp_path):
    _write(
        tmp_path, "s.py",
        "from othermod import *\n\n\n"
        "def h():\n    try:\n        work()\n"
        "    except ValueError as e:\n        report_error(e)\n",
    )
    r = _flake8(tmp_path, "s.py")
    assert r.returncode == 1 and "TBX001" in r.stdout, r.stdout


# 9. Reservation-gone: a plain local `def notify` and bare notify calls with
#    dynamic args draw ZERO TBX010/TBX011 - notify is no longer a reserved name.
def test_plain_notify_function_is_not_reserved(tmp_path):
    _write(
        tmp_path, "u.py",
        "def notify(user, text):\n    send(user, text)\n\n\n"
        "def welcome(u, greeting):\n    notify(u, greeting)\n",
    )
    r = _flake8(tmp_path, "u.py")
    assert r.returncode == 0, f"{r.stdout}\n{r.stderr}"


# 10. A function parameter named like a verb shadows the import within the
#     function.
def test_param_shadow_kills_within_function(tmp_path):
    _write(
        tmp_path, "p.py",
        "from tackbox_report import report_error\n\n\n"
        "def f(report_error):\n    try:\n        work()\n"
        "    except ValueError as e:\n        report_error(e)\n",
    )
    r = _flake8(tmp_path, "p.py")
    assert r.returncode == 1 and "TBX001" in r.stdout, r.stdout


# 11. Test-file exemption (D008 amend) is unchanged under origin: TBX010/TBX011
#     skip tests, but TBX001 still runs there.
def test_test_file_skips_gate_and_argcheck(tmp_path):
    # Broad except + dynamic msg: TBX010 and TBX011 would both fire outside tests;
    # in a test file both skip and notify still credits the path -> clean.
    _write(
        tmp_path, "test_x.py",
        "from tackbox_report import notify\n\n\n"
        "def test_h(msg_var):\n    try:\n        work()\n"
        "    except Exception as e:\n"
        "        notify(msg_var, cause=e, dedup_key='net.x')\n",
    )
    r = _flake8(tmp_path, "test_x.py")
    assert r.returncode == 0, f"{r.stdout}\n{r.stderr}"


def test_test_file_still_runs_swallow(tmp_path):
    # A shadow attack in a test file: TBX001 runs in tests, so the killed credit
    # surfaces the swallow.
    _write(
        tmp_path, "test_x.py",
        "from tackbox_report import report_error\n\n\n"
        "def report_error(x):\n    pass\n\n\n"
        "def test_h():\n    try:\n        work()\n"
        "    except ValueError as e:\n        report_error(e)\n",
    )
    r = _flake8(tmp_path, "test_x.py")
    assert r.returncode == 1 and "TBX001" in r.stdout, r.stdout


# 12. Owner package: a tackbox_report/ path segment self-credits its own verb
#     defs and skips TBX010/TBX011 - zero findings with a dynamic dedup key.
def test_owner_package_zero_findings(tmp_path):
    _write(
        tmp_path, "tackbox_report/core.py",
        "def notify(msg, cause=None, tags=None, dedup_key=''):\n    pass\n\n\n"
        "def report_error(msg, cause=None, tags=None, dedup_key=''):\n    pass\n\n\n"
        "def dispatch(name):\n    try:\n        work()\n"
        "    except Exception as e:\n"
        "        report_error('dispatch failed', cause=e, dedup_key=f'area.{name}')\n",
    )
    r = _flake8(tmp_path, "tackbox_report/core.py")
    assert r.returncode == 0, f"{r.stdout}\n{r.stderr}"


# 13. Tier-2 stays name-based, and its validation loosens to accept a facade
#     (a re-export import), while a name with neither def nor import stays a hard
#     dead-symbol error (exit 2).
def test_tier2_name_still_credits_with_argflow(tmp_path):
    _write(
        tmp_path, "app.py",
        "def sink(e):\n    print(e)\n\n\n"
        "def h():\n    try:\n        work()\n"
        "    except ValueError as e:\n        sink(e)\n",
    )
    r = _flake8(tmp_path, "app.py", reporters="app.py#sink")
    assert r.returncode == 0, f"{r.stdout}\n{r.stderr}"


def test_tier2_facade_import_binding_resolves(tmp_path):
    # A declared facade file whose only body is a re-export import passes tier-2
    # validation (loosened _has_top_level_def), and the consumer's bare call is
    # credited by the declared name.
    _write(tmp_path, "facade.py", "from tackbox_report import report_error\n")
    _write(
        tmp_path, "app.py",
        "def h():\n    try:\n        work()\n"
        "    except ValueError as e:\n        report_error(e)\n",
    )
    r = _flake8(tmp_path, "app.py", reporters="facade.py#report_error")
    assert r.returncode == 0, f"{r.stdout}\n{r.stderr}"


def test_tier2_plain_assignment_is_dead_symbol(tmp_path):
    _write(tmp_path, "facade.py", "report_error = object()\n")
    _write(tmp_path, "app.py", "def h():\n    pass\n")
    r = _flake8(tmp_path, "app.py", reporters="facade.py#report_error")
    assert r.returncode == 2, f"{r.stdout}\n{r.stderr}"
    assert "no top-level function report_error" in r.stderr, r.stderr
