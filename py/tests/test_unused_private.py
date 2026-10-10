"""TBX012 conservative module-private references and definition-time effects."""

from __future__ import annotations

import ast
import io
import tokenize

import pytest

from tackbox.pyrules.checker import Plugin


def _findings(source, filename="module.py"):
    tree = ast.parse(source)
    tokens = list(tokenize.generate_tokens(io.StringIO(source).readline))
    return [item for item in Plugin(tree, filename, tokens).analyze() if item[2] == "TBX012"]


@pytest.mark.parametrize("source", [
    "def _dead():\n    return _dead()\n",
    "async def _dead():\n    return await _dead()\n",
    "class _dead:\n    def recurse(self):\n        return _dead()\n",
    "def _():\n    return _()\n",
    "class _:\n    pass\n",
    "_dead = 1\n",
    "_dead: int = 1\n",
    "_dead = public = 1\n",
    "async with manager:\n    _dead = 1\n",
    "async for item in items:\n    _dead = 1\n",
    "try:\n    work()\nexcept* Error:\n    _dead = 1\n",
    "try:\n    work()\nexcept Error:\n    pass\nelse:\n    _dead = 1\nfinally:\n    _dead = 2\n",
    "if flag:\n    _dead = 1\nelse:\n    _dead = 2\n",
    "try:\n    from m import _dead\nexcept ImportError:\n    def _dead():\n        pass\n",
    "with manager:\n    _dead = 1\n",
    "for item in items:\n    _dead = 1\n",
    "while flag:\n    _dead = 1\n",
    "match value:\n    case 1:\n        _dead = 1\n",
    "_dead = 1\n'_dead'\n",
    "_dead = 1\ntext = '_dead'\n",
    "_dead = 1\n# _dead\n",
    "_dead = 1\ndef public():\n    '''_dead'''\n",
])
def test_unused_candidates_and_self_reference(source):
    findings = _findings(source)
    assert len(findings) == 1
    assert findings[0][3].startswith("`_`" if "def _(" in source or "class _:" in source else "`_dead`")
    assert findings[0][4:] == (None, None)


@pytest.mark.parametrize("source", [
    "_ = run()\n",
    "__private__ = 1\n",
    "_dead: int\n",
    "_a, _b = pair\n",
    "for _item in items:\n    pass\n",
    "with manager as _item:\n    pass\n",
    "try:\n    pass\nexcept Error as _error:\n    pass\n",
    "if (_value := run()):\n    pass\n",
    "match value:\n    case {'key': _item}:\n        pass\n",
    "import m as _m\n",
    "class Public:\n    _member = 1\n",
    "def public():\n    def _nested():\n        pass\n",
    "def public(unused):\n    return 1\n",
])
def test_non_candidates_and_parameters(source):
    assert _findings(source) == []


@pytest.mark.parametrize("source", [
    "_alias = registry\n_alias += [1]\n",
    "_counter = 0\n_counter += 1\n",
    "_dead = 1\ndel _dead\n",
    "_state = None\ndef get():\n    global _state\n    _state = start()\n    return _state\n",
    "def _f():\n    return 1\ndef test_f():\n    assert _f() == 1\n",
    "_x = 1\ndef public(_x):\n    return _x\n",
    "def _a():\n    return _b()\ndef _b():\n    return _a()\n",
    "def _child():\n    return 1\ndef _root():\n    return _child()\n_root()\n",
    "_width = 1\ntext = f'{value:{_width}}'\n",
    "@register\ndef _():\n    pass\n",
    "def _():\n    pass\n_('message')\n",
    "def _f():\n    pass\n_f = replacement\n_f()\n",
    "_f = 1\ndef _f(value=_f):\n    pass\n",
    "_f = 1\ndef _f() -> _f:\n    pass\n",
])
def test_conservative_reference_credit(source):
    assert _findings(source) == []


def test_dead_root_surfaces_before_its_callees():
    source = "def _child():\n    return 1\ndef _root():\n    return _child()\n"
    assert [item[0] for item in _findings(source)] == [3]


@pytest.mark.parametrize("body", [
    "    pass\n",
    "    constant = [1, {'a': -2}]\n",
    "    field: Descriptor\n",
    "    def method(self):\n        register(self)\n",
    "    class Nested:\n        constant = 1\n",
    "    if condition:\n        constant = 1\n",
    "    try:\n        constant = 1\n    finally:\n        pass\n",
])
def test_inert_private_classes_are_reported(body):
    assert len(_findings("class _dead(object):\n" + body)) == 1


@pytest.mark.parametrize("source", [
    "class _registered(Base):\n    pass\n",
    "class _registered(metaclass=Meta):\n    pass\n",
    "class _registered(flag=True):\n    pass\n",
    "@decorate\nclass _registered:\n    pass\n",
    "class _registered:\n    field = Descriptor()\n",
    "class _registered:\n    field = descriptor\n",
    "class _registered:\n    field: Field = descriptor\n",
    "class _registered:\n    public_state['ready'] = True\n",
    "class _registered:\n    public_state.ready = True\n",
    "class _registered:\n    public_state['count'] += 1\n",
    "class _registered:\n    del public_state.ready\n",
    "class _registered:\n    global public\n    public = 1\n",
    "class _registered:\n    @register\n    def callback(self):\n        pass\n",
    "class _registered:\n    def method(self, value=register()):\n        pass\n",
    "class _registered:\n    def method(self, value: register()):\n        pass\n",
    "class _registered:\n    def method(self) -> register():\n        pass\n",
    "class _registered:\n    field: register()\n",
    "class _registered:\n    if condition:\n        field = descriptor\n",
    "class _registered:\n    try:\n        public.ready = True\n    finally:\n        pass\n",
    "class _registered:\n    class Nested(Base):\n        pass\n",
    "class _registered:\n    @register\n    class Nested:\n        pass\n",
])
def test_class_definition_effects_are_credited(source):
    assert _findings(source) == []


@pytest.mark.parametrize("use", [
    "getattr(other, '_T')", "hasattr(other, '_T')",
    "setattr(other, '_T', value)", "delattr(other, '_T')",
    "globals()['_T']", "vars()['_T']", "dictionary['_T']",
    "x: '_T'", "def public(x: '_T') -> '_T':\n    pass",
    "X = Optional['_T']", "X: TypeAlias = '_T'",
    "X = list['_T' | None]", "X = tuple[int, '_T']", "dictionary['a', '_T']",
    "cast('_T', value)", "t.cast('_T', value)",
    "TypeVar('X', '_T', int)", "t.TypeVar('X', bound='_T')",
    "X = Annotated['_T', 'metadata']",
    "__all__ = ['_T']", "__all__ = ('_T',)",
    "__all__: list[str] = ['_T']", "__all__ = []\n__all__ += ['_T']",
    "__all__ = ['public'] + ['_T']",
    "from typing import cast as convert\nconvert('_T', value)",
    "from typing_extensions import TypeVar as TV\nTV('X', bound='_T')",
    "from typing import TypeAlias as TA\nX: TA = '_T'",
    "def public(*args: '_T', **kwargs: '_T'):\n    pass",
    "class Public:\n    def method(self, value: '_T'):\n        pass",
    "class Public(_T):\n    pass",
    "@decorate(_T)\ndef public():\n    pass",
    "def public(value: list['_T']):\n    pass",
    "from typing import Annotated as A\nA['_T', 'metadata']",
    "__all__ = []\n__all__ += ('_T',)",
])
def test_name_access_and_forward_references(use):
    assert _findings("class _T:\n    pass\n" + use + "\n") == []


@pytest.mark.parametrize("use", [
    "Literal['_T']", "typing.Literal['_T']", "t.Literal['_T']",
    "Annotated[int, '_T']", "Annotated[int, Optional['_T']]",
    "Optional[Literal['_T']]", "Optional[Annotated[int, '_T']]",
    "Literal[Optional['_T']]",
    "from typing import Literal as L\nL['_T']",
    "from typing_extensions import Annotated as A\nA[int, '_T']",
    "cast(Literal['_T'], value)", "TypeVar('X', bound=Literal['_T'])",
    "x: 'Literal[\"_T\"]'", "x: 'Annotated[int, \"_T\"]'",
    "cast('Literal[\"_T\"]', value)",
    "Literal[f'_T{suffix}']",
    "x: 'not a valid annotation!'", "getattr(other, prefix + '_T')",
    "globals()[f'_T{suffix}']", "data[str('_T')]", "data[make_type('_T')]",
    "data[key + '_T']", "data['_T' + key]", "data[lambda: '_T']",
    "data[value if condition else '_T']", "data[['_T']]", "data[{'key': '_T'}]",
    "data[(lambda: '_T').__name__]", "data[(key + '_T').attribute]",
])
def test_value_strings_and_dynamic_names_do_not_credit(use):
    assert len(_findings("class _T:\n    pass\n" + use + "\n")) == 1


def test_annotated_nested_type_and_metadata_are_distinct():
    source = "class _T:\n    pass\nclass _other:\n    pass\nX = Annotated[Optional['_T'], Literal['_other']]\n"
    assert [item[3].split('`')[1] for item in _findings(source)] == ['_other']


def test_test_files_and_type_checking_are_not_exempt():
    source = "if TYPE_CHECKING:\n    class _T:\n        pass\n"
    for filename in ("test_module.py", "tests/helpers.py", "conftest.py", "module.py"):
        assert len(_findings(source, filename)) == 1


def test_first_binding_anchors_one_finding():
    source = "if flag:\n    _dead = 1\nelse:\n    _dead = 2\n"
    assert [item[0] for item in _findings(source)] == [2]


def test_pep695_alias_candidates_and_self_reference():
    if not hasattr(ast, "TypeAlias"):
        pytest.skip("PEP 695 requires Python 3.12")
    assert len(_findings("type _T = list[_T]\n")) == 1
    assert len(_findings("type _ = int\n")) == 1
    assert _findings("class _T:\n    pass\ntype Public = '_T'\n") == []
    assert _findings("type _T = int\nx: _T\n") == []
    assert len(_findings("type _T = '_T'\n")) == 1
    assert _findings("type _T = int\ncast('_T', value)\n") == []
    for declaration in (
        "def public[T: '_T']():\n    pass\n",
        "class Public[T: '_T']:\n    pass\n",
        "type Public[T: '_T'] = list[T]\n",
    ):
        assert _findings("class _T:\n    pass\n" + declaration) == []


def test_class_definition_registration_is_observable():
    source = '''registry = []
class Base:
    def __init_subclass__(cls):
        registry.append(cls)
class _Registered(Base):
    pass
class Descriptor:
    def __set_name__(self, owner, name):
        registry.append(owner)
descriptor = Descriptor()
class _Described:
    field = descriptor
class Meta(type):
    def __new__(meta, name, bases, namespace):
        created = super().__new__(meta, name, bases, namespace)
        registry.append(created)
        return created
class _Meta(metaclass=Meta):
    pass
'''
    namespace = {}
    exec(source, namespace)
    assert [cls.__name__ for cls in namespace['registry']] == ['_Registered', '_Described', '_Meta']
    assert _findings(source) == []


def test_augassign_mutates_an_observed_shared_object():
    source = "registry = []\n_alias = registry\n_alias += [1]\n"
    namespace = {}
    exec(source, namespace)
    assert namespace['registry'] == [1]
    assert _findings(source) == []
