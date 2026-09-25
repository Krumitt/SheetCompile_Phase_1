"""
semantic_analyzer.py
=====================
Compiler-design concept: SEMANTIC ANALYSIS (static type checking + scope
checking), performed as its own pass BEFORE evaluation.

Phase 1's evaluator.py computes concrete values at runtime and only
discovers a type mismatch when it actually hits the bad value (e.g.
SUM() tries to coerce a text cell to a number mid-evaluation). A real
compiler front end instead has a dedicated SEMANTIC ANALYSIS phase
that walks the AST *before* code generation / execution and rejects
programs that can never be valid, with a clear diagnostic pointing at
the offending cell.

This module adds that phase to SheetCompile:

  - Type: the small type lattice we reason about (NUMBER, STRING,
    BOOLEAN, RANGE, UNKNOWN).
  - TypedSymbolTable: extends Phase 1's SymbolTable (symbol_table.py)
    with per-cell inferred-type tracking, instead of duplicating its
    cell/named-range bookkeeping.
  - SemanticAnalyzer: a genuine VISITOR over ast_nodes.py's node
    classes (one visit_<NodeType> method per class, dispatched by a
    table keyed on type(node) -- not the ad-hoc isinstance-chain style
    evaluator.py uses -- so this pass is easy to defend on its own in
    a viva).
  - analyze_workbook(): the driver that runs the analyzer over every
    formula cell in dependency order, mirroring how main.py drives
    evaluate() over the same order.

Design note on UNKNOWN: a cell that hasn't been analyzed yet (forward
reference) or whose value genuinely can't be pinned down statically
(e.g. the result of VLOOKUP) is typed UNKNOWN rather than rejected.
UNKNOWN is treated as "give it the benefit of the doubt" everywhere a
NUMBER/BOOLEAN is expected, exactly like a compiler's type checker
treats an `Any`/unresolved type -- this keeps the analyzer sound
without making it so strict it rejects legitimate formulas just
because it can't prove them safe in one static pass.
"""

from ast_nodes import (
    NumberNode,
    StringNode,
    BooleanNode,
    CellRefNode,
    RangeNode,
    NamedRangeNode,
    UnaryOpNode,
    BinaryOpNode,
    FunctionCallNode,
)
from error_reporter import SemanticError
from language_spec import SUPPORTED_FUNCTIONS
from symbol_table import SymbolTable


# ---------------------------------------------------------------------------
# The type lattice
# ---------------------------------------------------------------------------
class Type:
    """String-constant "enum", kept plain (like language_spec.py's TT_*
    constants) so error messages can just interpolate the value directly."""

    NUMBER = "NUMBER"
    STRING = "STRING"
    BOOLEAN = "BOOLEAN"
    RANGE = "RANGE"
    UNKNOWN = "UNKNOWN"  # not yet analyzed, or genuinely data-dependent

    ALL = {NUMBER, STRING, BOOLEAN, RANGE, UNKNOWN}


def infer_literal_type(raw_value):
    """Infers the Type of a non-formula cell's literal value. bool is
    checked before (int, float) because in Python `bool` is a subclass
    of `int` -- True would otherwise be misclassified as NUMBER."""
    if isinstance(raw_value, bool):
        return Type.BOOLEAN
    if isinstance(raw_value, (int, float)):
        return Type.NUMBER
    if isinstance(raw_value, str):
        return Type.STRING
    return Type.UNKNOWN


def _join(t1, t2):
    """Combines the types of two branches of a formula (e.g. IF's two
    result arms) into a single reported type: identical -> that type,
    otherwise we can't promise more than UNKNOWN."""
    return t1 if t1 == t2 else Type.UNKNOWN


# ---------------------------------------------------------------------------
# Symbol table with type tracking
# ---------------------------------------------------------------------------
class TypedSymbolTable(SymbolTable):
    """Extends Phase 1's SymbolTable (symbol_table.py) rather than
    re-implementing cell/named-range storage: this class IS-A
    SymbolTable, plus a per-(sheet, cell) inferred-type map.

    Requirement 1 (track identifiers, cell refs like A1/B2:B10, and
    their inferred data types) is satisfied by the combination of the
    inherited _cells / _named_ranges maps and this class's _types map.
    """

    def __init__(self):
        super().__init__()
        self._types = {}  # (sheet, cell) -> Type

    def set_type(self, sheet, cell, type_):
        if type_ not in Type.ALL:
            raise ValueError(f"not a valid Type: {type_!r}")
        self._types[(sheet, cell)] = type_

    def get_type(self, sheet, cell):
        """Unanalyzed / unknown cells default to Type.UNKNOWN rather
        than None, so callers never need a separate None-check."""
        return self._types.get((sheet, cell), Type.UNKNOWN)

    def resolve_named_range_type(self, name):
        """A named range's type is derived, not stored directly: a
        multi-cell reference (contains ':') is a RANGE; a single-cell
        reference just forwards that cell's tracked type."""
        resolved = self.lookup_named_range(name)
        if resolved is None:
            return None  # caller distinguishes "undefined" from "typed"
        sheet, ref = resolved
        if ":" in ref:
            return Type.RANGE
        return self.get_type(sheet, ref.replace("$", ""))

    def __repr__(self):
        return (
            f"TypedSymbolTable({len(self._cells)} cells, "
            f"{len(self._named_ranges)} named ranges, "
            f"{len(self._types)} typed)"
        )


# ---------------------------------------------------------------------------
# Function signatures: (min_args, max_args, category)
# Mirrors the argument-count contracts evaluator.py enforces at runtime
# (_require_arg_count), so a bad call is caught here, statically, first.
# ---------------------------------------------------------------------------
NUMERIC_AGGREGATE = "NUMERIC_AGGREGATE"  # SUM, AVERAGE, MIN, MAX ...
COUNT_LIKE = "COUNT_LIKE"  # COUNT, COUNTA (any type allowed)
CRITERIA_AGGREGATE = "CRITERIA_AGGREGATE"  # COUNTIF, SUMIF, SUMIFS
CONDITIONAL = "CONDITIONAL"  # IF, IFS
LOGICAL = "LOGICAL"  # AND, OR, NOT
LOOKUP = "LOOKUP"  # VLOOKUP, HLOOKUP, INDEX, MATCH
STRING_FN = "STRING_FN"  # CONCAT, TEXT, LEFT, RIGHT, ...
ERROR_HANDLING = "ERROR_HANDLING"  # IFERROR, ISERROR
DATE_FN = "DATE_FN"  # DATEDIF

FUNCTION_SIGNATURES = {
    "SUM": (1, None, NUMERIC_AGGREGATE),
    "AVERAGE": (1, None, NUMERIC_AGGREGATE),
    "MIN": (1, None, NUMERIC_AGGREGATE),
    "MAX": (1, None, NUMERIC_AGGREGATE),
    "COUNT": (1, None, COUNT_LIKE),
    "COUNTA": (1, None, COUNT_LIKE),
    "COUNTIF": (2, 2, CRITERIA_AGGREGATE),
    "SUMIF": (2, 3, CRITERIA_AGGREGATE),
    "SUMIFS": (3, None, CRITERIA_AGGREGATE),
    "IF": (2, 3, CONDITIONAL),
    "IFS": (2, None, CONDITIONAL),
    "AND": (1, None, LOGICAL),
    "OR": (1, None, LOGICAL),
    "NOT": (1, 1, LOGICAL),
    "VLOOKUP": (3, 4, LOOKUP),
    "HLOOKUP": (3, 4, LOOKUP),
    "INDEX": (2, 2, LOOKUP),
    "MATCH": (2, 3, LOOKUP),
    "DATEDIF": (3, 3, DATE_FN),
    "CONCAT": (1, None, STRING_FN),
    "CONCATENATE": (1, None, STRING_FN),
    "TEXT": (2, 2, STRING_FN),
    "LEFT": (1, 2, STRING_FN),
    "RIGHT": (1, 2, STRING_FN),
    "MID": (3, 3, STRING_FN),
    "TRIM": (1, 1, STRING_FN),
    "IFERROR": (2, 2, ERROR_HANDLING),
    "ISERROR": (1, 1, ERROR_HANDLING),
}

# Types that may flow into an arithmetic / numeric-aggregate position.
# BOOLEAN is included because Excel (and evaluator._as_number) coerces
# TRUE/FALSE to 1/0; UNKNOWN is included per the "benefit of the doubt"
# rule above.
_NUMERIC_COMPATIBLE = {Type.NUMBER, Type.BOOLEAN, Type.RANGE, Type.UNKNOWN}
_LOGICAL_COMPATIBLE = {Type.NUMBER, Type.BOOLEAN, Type.UNKNOWN}


class SemanticAnalyzer:
    """Visitor over the AST classes defined in ast_nodes.py. One
    visit_<Type> method per node class; dispatch() below is the only
    place that maps a node's Python type to its handler, which is the
    textbook Visitor pattern (vs. evaluator.py's inline isinstance
    chain, which is deliberately kept style-distinct)."""

    def __init__(self, symbol_table: TypedSymbolTable, known_sheets=None):
        self.symtab = symbol_table
        self.known_sheets = known_sheets  # optional set[str], for scope checks
        self.current_sheet = None
        self.current_cell = None
        self._dispatch = {
            NumberNode: self.visit_number,
            StringNode: self.visit_string,
            BooleanNode: self.visit_boolean,
            CellRefNode: self.visit_cell_ref,
            RangeNode: self.visit_range,
            NamedRangeNode: self.visit_named_range,
            UnaryOpNode: self.visit_unary_op,
            BinaryOpNode: self.visit_binary_op,
            FunctionCallNode: self.visit_function_call,
        }

    # ---- public entry point ------------------------------------------
    def analyze_cell(self, ast_root, sheet, cell):
        """Type-checks one cell's formula AST and records its inferred
        type in the symbol table. Raises SemanticError on the first
        problem found in that cell (mirrors ParseError/LexError: one
        bad cell doesn't stop the whole pass -- see analyze_workbook)."""
        self.current_sheet = sheet
        self.current_cell = cell
        result_type = self.visit(ast_root)
        self.symtab.set_type(sheet, cell, result_type)
        return result_type

    # ---- dispatch -------------------------------------------------------
    def visit(self, node):
        handler = self._dispatch.get(type(node))
        if handler is None:
            raise self._error(f"no semantic rule for AST node {type(node).__name__}")
        return handler(node)

    def _error(self, message):
        return SemanticError(message, sheet=self.current_sheet, cell=self.current_cell)

    # ---- literals -------------------------------------------------------
    def visit_number(self, node: NumberNode):
        return Type.NUMBER

    def visit_string(self, node: StringNode):
        return Type.STRING

    def visit_boolean(self, node: BooleanNode):
        return Type.BOOLEAN

    # ---- references (scope checking lives here) --------------------------
    def visit_cell_ref(self, node: CellRefNode):
        self._check_sheet_scope(node.sheet)
        sheet = node.sheet or self.current_sheet
        return self.symtab.get_type(sheet, node.cell.replace("$", ""))

    def visit_range(self, node: RangeNode):
        self._check_sheet_scope(node.sheet)
        return Type.RANGE

    def visit_named_range(self, node: NamedRangeNode):
        resolved_type = self.symtab.resolve_named_range_type(node.name)
        if resolved_type is None:
            raise self._error(f"undefined named range '{node.name}'")
        return resolved_type

    def _check_sheet_scope(self, sheet_name):
        """Scope validation: a reference like OtherSheet!A1 is only
        valid if OtherSheet is a real sheet in this workbook. Skipped
        when the analyzer wasn't given the workbook's sheet list."""
        if sheet_name is not None and self.known_sheets is not None:
            if sheet_name not in self.known_sheets:
                raise self._error(f"reference to undefined sheet '{sheet_name}'")

    # ---- operators ------------------------------------------------------
    def visit_unary_op(self, node: UnaryOpNode):
        operand_type = self.visit(node.operand)
        if node.op == "-":
            if operand_type not in _NUMERIC_COMPATIBLE:
                raise self._error(
                    f"unary '-' expects a numeric operand, got {operand_type}"
                )
            return Type.NUMBER
        raise self._error(f"unknown unary operator '{node.op}'")

    def visit_binary_op(self, node: BinaryOpNode):
        left_type = self.visit(node.left)
        right_type = self.visit(node.right)
        op = node.op

        if op == "&":
            if Type.RANGE in (left_type, right_type):
                raise self._error(
                    "'&' cannot concatenate a whole range directly "
                    "(use TEXT()/CONCAT() over individual cells)"
                )
            return Type.STRING

        if op in ("=", "<>", "<", ">", "<=", ">="):
            if Type.RANGE in (left_type, right_type):
                raise self._error(f"'{op}' cannot compare a whole range")
            return Type.BOOLEAN

        # arithmetic: + - * / ^
        for side, t in (("left", left_type), ("right", right_type)):
            if t not in _NUMERIC_COMPATIBLE:
                raise self._error(
                    f"'{op}' expects numeric operands, {side} side is {t}"
                )
        return Type.NUMBER

    # ---- function calls ---------------------------------------------------
    def visit_function_call(self, node: FunctionCallNode):
        name = node.name.upper()

        if name not in SUPPORTED_FUNCTIONS:
            raise self._error(f"undefined function '{node.name}'")

        min_args, max_args, category = FUNCTION_SIGNATURES[name]
        n = len(node.args)
        if n < min_args or (max_args is not None and n > max_args):
            expected = (
                f"{min_args}"
                if min_args == max_args
                else (
                    f"at least {min_args}"
                    if max_args is None
                    else f"{min_args}-{max_args}"
                )
            )
            raise self._error(f"{name} expects {expected} argument(s), got {n}")

        # Visit every argument first so nested errors surface too,
        # regardless of which category-specific check below fires.
        arg_types = [self.visit(a) for a in node.args]

        if category == NUMERIC_AGGREGATE:
            for i, t in enumerate(arg_types):
                if t not in _NUMERIC_COMPATIBLE:
                    raise self._error(
                        f"{name}() expects numeric values or a range, "
                        f"argument {i + 1} is {t}"
                    )
            return Type.NUMBER

        if category == COUNT_LIKE:
            return Type.NUMBER  # COUNT/COUNTA accept any type by design

        if category == CRITERIA_AGGREGATE:
            if arg_types[0] != Type.RANGE and arg_types[0] != Type.UNKNOWN:
                raise self._error(
                    f"{name}() expects a range as its first argument, "
                    f"got {arg_types[0]}"
                )
            if name == "SUMIFS" and n % 2 == 0:
                raise self._error(
                    "SUMIFS expects sum_range followed by (range, criterion) pairs "
                    "(an odd total argument count)"
                )
            return Type.NUMBER

        if category == CONDITIONAL:
            if name == "IF":
                if arg_types[0] == Type.RANGE:
                    raise self._error("IF's condition cannot be a whole range")
                if n == 3:
                    return _join(arg_types[1], arg_types[2])
                return arg_types[1]
            # IFS: (condition, value)+ pairs
            if n % 2 != 0:
                raise self._error(
                    "IFS expects an even number of (condition, value) arguments"
                )
            value_types = arg_types[1::2]
            result = value_types[0]
            for t in value_types[1:]:
                result = _join(result, t)
            return result

        if category == LOGICAL:
            for i, t in enumerate(arg_types):
                if t not in _LOGICAL_COMPATIBLE:
                    raise self._error(
                        f"{name}() expects logical/numeric values, "
                        f"argument {i + 1} is {t}"
                    )
            return Type.BOOLEAN

        if category == LOOKUP:
            table_arg = node.args[1] if name in ("VLOOKUP", "HLOOKUP") else node.args[0]
            table_type = (
                arg_types[1] if name in ("VLOOKUP", "HLOOKUP") else arg_types[0]
            )
            if not isinstance(table_arg, RangeNode) and table_type != Type.UNKNOWN:
                raise self._error(f"{name}()'s table/range argument must be a range")
            return Type.UNKNOWN  # genuinely data-dependent

        if category == STRING_FN:
            return Type.STRING

        if category == ERROR_HANDLING:
            if name == "IFERROR":
                return _join(arg_types[0], arg_types[1])
            return Type.BOOLEAN  # ISERROR

        if category == DATE_FN:
            return Type.NUMBER  # day/month/year difference

        raise self._error(
            f"no semantic rule registered for function category {category}"
        )


# ---------------------------------------------------------------------------
# Driver: analyze every formula cell in dependency order
# ---------------------------------------------------------------------------
def analyze_workbook(wb, asts, symtab: TypedSymbolTable, order, errors):
    """Mirrors main.py's evaluate loop, but for static type-checking.

    wb      - ExtractedWorkbook (extractor.py), for literal cell values
    asts    - dict[(sheet, cell) -> AST root], already parsed
    symtab  - TypedSymbolTable, pre-populated with cells/named ranges
              (same population step main.py already does with
              SymbolTable -- just construct a TypedSymbolTable instead)
    order   - topological order from DependencyGraph.topological_order()
    errors  - shared ErrorReport (error_reporter.py) to accumulate into

    Analyzing in dependency order means that by the time a formula
    references another cell, that cell's type has already been set --
    so most CellRefNode lookups resolve to a real type rather than
    falling back to UNKNOWN.
    """
    analyzer = SemanticAnalyzer(symtab, known_sheets=set(wb.sheets))

    for sheet, cell in order:
        entry = wb.get_cell(sheet, cell)
        if entry is None:
            continue

        if not entry.is_formula:
            symtab.set_type(sheet, cell, infer_literal_type(entry.raw_value))
            continue

        ast_root = asts.get((sheet, cell))
        if ast_root is None:
            continue  # already reported as a ParseError upstream

        try:
            analyzer.analyze_cell(ast_root, sheet, cell)
        except SemanticError as e:
            errors.add(e)
            symtab.set_type(sheet, cell, Type.UNKNOWN)  # don't cascade the failure

    return symtab, errors


# ---------------------------------------------------------------------------
# Self-test: run directly with `python semantic_analyzer.py` to sanity-check
# this module in isolation, without needing a real .xlsx workbook.
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    from parser import parse_formula

    PASS, FAIL = "PASS", "FAIL"
    _results = []

    def _check(label, fn):
        try:
            fn()
            _results.append((label, PASS, ""))
        except AssertionError as e:
            _results.append((label, FAIL, str(e)))
        except Exception as e:  # noqa: BLE001 - want to report any failure, not crash
            _results.append((label, FAIL, f"{type(e).__name__}: {e}"))

    def _analyze(formula, symtab):
        ast_root = parse_formula(formula, sheet="Sheet1", cell="Z1")
        return SemanticAnalyzer(symtab).analyze_cell(ast_root, "Sheet1", "Z1")

    def _build_symtab():
        st = TypedSymbolTable()
        st.insert_cell("Sheet1", "A1", is_formula=False, raw_value=10)
        st.set_type("Sheet1", "A1", Type.NUMBER)
        st.insert_cell("Sheet1", "A2", is_formula=False, raw_value="hello")
        st.set_type("Sheet1", "A2", Type.STRING)
        st.insert_cell("Sheet1", "A3", is_formula=False, raw_value=True)
        st.set_type("Sheet1", "A3", Type.BOOLEAN)
        st.insert_named_range("TaxRate", "Sheet1", "A1")
        return st

    def test_numeric_arithmetic_ok():
        t = _analyze("=A1+5", _build_symtab())
        assert t == Type.NUMBER, f"expected NUMBER, got {t}"

    def test_string_concat_ok():
        t = _analyze('=A2&"!"', _build_symtab())
        assert t == Type.STRING, f"expected STRING, got {t}"

    def test_sum_over_range_ok():
        t = _analyze("=SUM(A1:A10)", _build_symtab())
        assert t == Type.NUMBER, f"expected NUMBER, got {t}"

    def test_named_range_resolves():
        t = _analyze("=TaxRate*2", _build_symtab())
        assert t == Type.NUMBER, f"expected NUMBER, got {t}"

    def test_if_branch_join():
        t = _analyze('=IF(A1>5,"big",1)', _build_symtab())
        assert t == Type.UNKNOWN, f"expected UNKNOWN (branch types differ), got {t}"

    def test_arithmetic_on_text_rejected():
        try:
            _analyze("=A2+1", _build_symtab())
            raise AssertionError("expected a SemanticError, none was raised")
        except SemanticError:
            pass

    def test_sum_over_text_literal_rejected():
        try:
            _analyze('=SUM("abc", 1)', _build_symtab())
            raise AssertionError("expected a SemanticError, none was raised")
        except SemanticError:
            pass

    def test_undefined_named_range_rejected():
        try:
            _analyze("=NoSuchName+1", _build_symtab())
            raise AssertionError("expected a SemanticError, none was raised")
        except SemanticError:
            pass

    def test_undefined_function_rejected():
        try:
            _analyze("=FAKEFUNC(A1)", _build_symtab())
            raise AssertionError("expected a SemanticError, none was raised")
        except SemanticError:
            pass

    def test_wrong_arg_count_rejected():
        try:
            _analyze("=NOT(A1,A2)", _build_symtab())
            raise AssertionError("expected a SemanticError, none was raised")
        except SemanticError:
            pass

    def test_vlookup_requires_range():
        try:
            _analyze("=VLOOKUP(A1,A2,1)", _build_symtab())
            raise AssertionError("expected a SemanticError, none was raised")
        except SemanticError:
            pass

    _check("arithmetic over a numeric cell -> NUMBER", test_numeric_arithmetic_ok)
    _check("'&' concatenation -> STRING", test_string_concat_ok)
    _check("SUM() over a range -> NUMBER", test_sum_over_range_ok)
    _check("named range resolves to underlying type", test_named_range_resolves)
    _check("IF() with differing branch types -> UNKNOWN", test_if_branch_join)
    _check("arithmetic on text cell is rejected", test_arithmetic_on_text_rejected)
    _check("SUM() over a text literal is rejected", test_sum_over_text_literal_rejected)
    _check("undefined named range is rejected", test_undefined_named_range_rejected)
    _check("undefined function is rejected", test_undefined_function_rejected)
    _check("wrong argument count is rejected", test_wrong_arg_count_rejected)
    _check("VLOOKUP with non-range table arg is rejected", test_vlookup_requires_range)

    print("=" * 60)
    print("semantic_analyzer.py self-test")
    print("=" * 60)
    for label, status, detail in _results:
        tag = (
            f"\033[92m{status}\033[0m" if status == PASS else f"\033[91m{status}\033[0m"
        )
        print(f"[{tag}] {label}" + (f"  -> {detail}" if detail else ""))

    n_fail = sum(1 for _, status, _ in _results if status == FAIL)
    print("-" * 60)
    print(f"{len(_results) - n_fail}/{len(_results)} checks passed")
    if n_fail:
        raise SystemExit(1)
