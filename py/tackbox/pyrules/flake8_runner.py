"""Run flake8 with Tackbox's naming and output conventions."""

from __future__ import annotations

import ast
import json
import tokenize

from flake8.formatting.base import BaseFormatter
from flake8.formatting.default import Default
from flake8.main.application import Application
from pyflakes.checker import Importation

from .codes import CODE_TO_ID, PYFLAKES_CODES


class _FilteredFormatter(BaseFormatter):
    def __init__(self, options):
        super().__init__(options)
        self.emitted = 0
        self._side_effect_imports = {}

    def handle(self, error):
        if error.code == "F401":
            if error.filename not in self._side_effect_imports:
                with tokenize.open(error.filename) as source:
                    tree = ast.parse(source.read(), filename=error.filename)
                self._side_effect_imports[error.filename] = {
                    (node.lineno, f"'{Importation(alias.asname, node, alias.name)}' imported but unused")
                    for node in ast.walk(tree) if isinstance(node, ast.Import)
                    for alias in node.names if alias.asname and alias.asname.startswith("_")
                }
            if (error.line_number, error.text) in self._side_effect_imports[error.filename]:
                return
        self.emitted += 1
        super().handle(error)


class _HumanFormatter(_FilteredFormatter, Default):
    def format(self, error):
        if error.code in PYFLAKES_CODES:
            error = error._replace(text=f"{CODE_TO_ID[error.code]}: {error.text}")
        return super().format(error)


class _MachineFormatter(_FilteredFormatter):
    def format(self, error):
        rule = CODE_TO_ID[error.code]
        message = error.text.removeprefix(rule + ": ")
        return json.dumps({
            "file": error.filename, "line": error.line_number,
            "rule": rule, "message": message,
        })


def run(files: list[str], flags: list[str], machine: bool) -> int:
    app = Application()
    app.initialize([*flags, *files])
    formatter = (_MachineFormatter if machine else _HumanFormatter)(app.options)
    app.formatter = formatter
    app.make_guide()
    app.make_file_checker_manager([*flags, *files])
    app.run_checks()
    if app.catastrophic_failure:
        raise RuntimeError("flake8 could not complete its checks")
    app.report()
    return formatter.emitted
