"""
test_pipeline.py
=================
Runs the full Phase 1 pipeline (extract -> lex -> parse -> AST ->
symbol table -> dependency graph -> evaluate) against the sample
workbooks and prints a clear pass/fail report per cell, per the
Phase 1 deliverable requirement.

Run with:  python tests/test_pipeline.py
(run from the sheetcompile/ project root, or adjust sys.path below)
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from main import run_pipeline
from evaluator import evaluate, EvalContext
from symbol_table import SymbolTable
from parser import parse_formula
from error_reporter import SemanticError, DependencyError

from tests.expected_values import PRICING_EXPECTED, PAYROLL_EXPECTED

WORKBOOK_DIR = os.path.join(os.path.dirname(__file__), "workbooks")

PASS, FAIL = "PASS", "FAIL"
_results = []


def _record(label, ok, detail=""):
    _results.append((label, PASS if ok else FAIL, detail))
    tag = "\033[92mPASS\033[0m" if ok else "\033[91mFAIL\033[0m"
    print(f"[{tag}] {label}" + (f"  ({detail})" if detail else ""))


def test_workbook_against_expected(name, expected):
    path = os.path.join(WORKBOOK_DIR, name)
    values, errors = run_pipeline(path, verbose=False)

    if errors.has_errors():
        _record(f"{name}: pipeline ran without errors", False,
                 detail="; ".join(str(e) for e in errors.errors))
        return

    for key, expected_val in expected.items():
        sheet, cell = key
        actual = values.get(key)
        ok = _values_close(actual, expected_val)
        _record(f"{name}: {sheet}!{cell} == {expected_val!r}", ok,
                 detail=f"got {actual!r}")


def _values_close(a, b, tol=1e-9):
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return abs(a - b) < tol
    return a == b


def test_circular_reference_detected():
    """inventory.xlsx deliberately contains D1=D2+1, D2=D1+1 -- the
    pipeline must detect this as a DependencyError, not silently loop
    or crash."""
    path = os.path.join(WORKBOOK_DIR, "inventory.xlsx")
    values, errors = run_pipeline(path, verbose=False)
    found_cycle_error = any(
        "circular" in str(e).lower() for e in errors.errors
    )
    _record("inventory.xlsx: circular reference is detected", found_cycle_error,
             detail="; ".join(str(e) for e in errors.errors) if not found_cycle_error else "")


def test_type_error_detected():
    """A deliberate type error: SUM() over a text cell must raise a
    SemanticError rather than silently returning a wrong number.
    Tested directly against the evaluator (isolated from the
    circular-reference cells in the same workbook, which would
    otherwise block the whole pipeline's evaluation phase first)."""
    symtab = SymbolTable()
    ctx = EvalContext(
        current_sheet="Sheet1",
        symbol_table=symtab,
        computed_values={("Sheet1", "E1"): "Not A Number"},
    )
    ast_root = parse_formula("=SUM(E1)", sheet="Sheet1", cell="E2")
    try:
        evaluate(ast_root, ctx)
        _record("SUM() over text raises SemanticError", False, detail="no exception raised")
    except SemanticError as e:
        _record("SUM() over text raises SemanticError", True, detail=str(e))


def test_lexer_reports_illegal_character():
    from lexer import Lexer
    from error_reporter import LexError
    try:
        Lexer("=A1@B1", sheet="Sheet1", cell="Z1").tokenize()
        _record("lexer rejects illegal character '@'", False, detail="no exception raised")
    except LexError as e:
        _record("lexer rejects illegal character '@'", True, detail=str(e))


def test_parser_reports_missing_paren():
    from error_reporter import ParseError
    try:
        parse_formula("=SUM(A1:A5", sheet="Sheet1", cell="Z2")
        _record("parser rejects missing ')'", False, detail="no exception raised")
    except ParseError as e:
        _record("parser rejects missing ')'", True, detail=str(e))


def main():
    print("=" * 70)
    print("SheetCompile Phase 1 -- Test Suite")
    print("=" * 70)

    test_workbook_against_expected("pricing.xlsx", PRICING_EXPECTED)
    test_workbook_against_expected("payroll.xlsx", PAYROLL_EXPECTED)
    test_circular_reference_detected()
    test_type_error_detected()
    test_lexer_reports_illegal_character()
    test_parser_reports_missing_paren()

    print("=" * 70)
    passed = sum(1 for _, status, _ in _results if status == PASS)
    total = len(_results)
    print(f"Result: {passed}/{total} checks passed")
    print("=" * 70)

    if passed != total:
        sys.exit(1)


if __name__ == "__main__":
    main()
