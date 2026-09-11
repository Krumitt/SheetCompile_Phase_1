"""
evaluator.py
============
Compiler-design concept: SEMANTIC ANALYSIS + INTERPRETATION.

Walks a cell's AST (in the topological order computed by
dependency_graph.py) and computes its concrete value, implementing the
actual semantics of every function in the supported subset. This is
the Phase 1 "correctness baseline": its output must match what Excel
itself computed for the same cell, which is what tests/test_pipeline.py
checks.

Design note: a single `evaluate(node, context)` function dispatches on
node type (visitor-style, without a formal Visitor class, to keep the
tree-walk readable for a viva). Range/aggregate functions resolve a
RangeNode into a flat list of already-computed values via the context.
"""

import re
from datetime import date, timedelta

from ast_nodes import (
    NumberNode, StringNode, BooleanNode, CellRefNode, RangeNode, NamedRangeNode,
    UnaryOpNode, BinaryOpNode, FunctionCallNode,
)
from error_reporter import SemanticError


class EvalContext:
    """Carries everything the evaluator needs to resolve references:
    which sheet we're currently evaluating in, the symbol table (for
    named ranges), and a dict of already-computed values for
    (sheet, cell) keys, filled in topological order by main.py."""

    def __init__(self, current_sheet, symbol_table, computed_values):
        self.current_sheet = current_sheet
        self.symbol_table = symbol_table
        self.computed_values = computed_values  # (sheet, cell) -> value

    def get_cell_value(self, sheet, cell):
        sheet = sheet or self.current_sheet
        return self.computed_values.get((sheet, cell))


# --- cell reference arithmetic (for expanding ranges) ----------------------
_CELL_RE = re.compile(r"^\$?([A-Z]+)\$?(\d+)$")


def _col_to_index(col_letters):
    idx = 0
    for ch in col_letters:
        idx = idx * 26 + (ord(ch) - ord("A") + 1)
    return idx


def _index_to_col(idx):
    letters = ""
    while idx > 0:
        idx, rem = divmod(idx - 1, 26)
        letters = chr(ord("A") + rem) + letters
    return letters


def expand_range(start_cell, end_cell):
    """Returns the list of individual cell names (e.g. ['A1','A2','B1','B2'])
    covered by a range like A1:B2, in row-major order."""
    m1, m2 = _CELL_RE.match(start_cell), _CELL_RE.match(end_cell)
    if not m1 or not m2:
        raise SemanticError(f"invalid range bounds {start_cell}:{end_cell}")
    c1, r1 = _col_to_index(m1.group(1)), int(m1.group(2))
    c2, r2 = _col_to_index(m2.group(1)), int(m2.group(2))
    cells = []
    for r in range(min(r1, r2), max(r1, r2) + 1):
        for c in range(min(c1, c2), max(c1, c2) + 1):
            cells.append(f"{_index_to_col(c)}{r}")
    return cells


def _clean_ref(cell):
    """Strips '$' anchors so 'A1', '$A$1', 'A$1' all resolve the same cell."""
    return cell.replace("$", "")


# --- main evaluate() dispatch ------------------------------------------------
def evaluate(node, ctx: EvalContext):
    if isinstance(node, NumberNode):
        return node.value

    if isinstance(node, StringNode):
        return node.value

    if isinstance(node, BooleanNode):
        return node.value

    if isinstance(node, CellRefNode):
        val = ctx.get_cell_value(node.sheet, _clean_ref(node.cell))
        return 0 if val is None else val

    if isinstance(node, RangeNode):
        # A bare range outside a function (rare) resolves to a list of values.
        return _resolve_range_values(node, ctx)

    if isinstance(node, NamedRangeNode):
        resolved = ctx.symbol_table.lookup_named_range(node.name)
        if resolved is None:
            raise SemanticError(f"undefined named range '{node.name}'")
        sheet, ref = resolved
        if ":" in ref:
            start, end = ref.split(":")
            return _resolve_range_values(RangeNode(start, end, sheet=sheet), ctx)
        val = ctx.get_cell_value(sheet, _clean_ref(ref))
        return 0 if val is None else val

    if isinstance(node, UnaryOpNode):
        val = evaluate(node.operand, ctx)
        if node.op == "-":
            return -_as_number(val)
        raise SemanticError(f"unknown unary operator {node.op}")

    if isinstance(node, BinaryOpNode):
        return _evaluate_binary(node, ctx)

    if isinstance(node, FunctionCallNode):
        return _evaluate_function(node, ctx)

    raise SemanticError(f"unknown AST node type {type(node).__name__}")


def _resolve_range_values(node: RangeNode, ctx: EvalContext):
    cells = expand_range(_clean_ref(node.start_cell), _clean_ref(node.end_cell))
    return [ctx.get_cell_value(node.sheet, c) for c in cells]


def _as_number(v):
    if v is None:
        return 0
    if isinstance(v, bool):
        return 1 if v else 0
    if isinstance(v, (int, float)):
        return v
    if isinstance(v, str):
        try:
            return float(v)
        except ValueError:
            raise SemanticError(f"cannot treat text {v!r} as a number")
    raise SemanticError(f"cannot treat {v!r} as a number")


def _evaluate_binary(node: BinaryOpNode, ctx):
    op = node.op
    left = evaluate(node.left, ctx)
    right = evaluate(node.right, ctx)

    if op == "&":
        return str(left) + str(right)

    if op in ("=", "<>", "<", ">", "<=", ">="):
        try:
            l, r = _as_number(left), _as_number(right)
        except SemanticError:
            l, r = left, right  # fall back to string comparison
        if op == "=":
            return l == r
        if op == "<>":
            return l != r
        if op == "<":
            return l < r
        if op == ">":
            return l > r
        if op == "<=":
            return l <= r
        if op == ">=":
            return l >= r

    l, r = _as_number(left), _as_number(right)
    if op == "+":
        return l + r
    if op == "-":
        return l - r
    if op == "*":
        return l * r
    if op == "/":
        if r == 0:
            raise SemanticError("division by zero (#DIV/0!)")
        return l / r
    if op == "^":
        return l ** r

    raise SemanticError(f"unknown binary operator {op}")


# --- function implementations -------------------------------------------------
def _flatten_arg(arg_node, ctx):
    """Turns any argument (single cell, range, literal, nested call)
    into a flat Python list of values, so aggregate functions can treat
    every argument uniformly."""
    if isinstance(arg_node, RangeNode):
        return _resolve_range_values(arg_node, ctx)
    val = evaluate(arg_node, ctx)
    return val if isinstance(val, list) else [val]


def _evaluate_function(node: FunctionCallNode, ctx):
    name = node.name
    args = node.args

    if name == "SUM":
        vals = [v for a in args for v in _flatten_arg(a, ctx)]
        return sum(_as_number(v) for v in vals if v is not None)

    if name == "AVERAGE":
        vals = [_as_number(v) for a in args for v in _flatten_arg(a, ctx) if v is not None]
        if not vals:
            raise SemanticError("AVERAGE of an empty range (#DIV/0!)")
        return sum(vals) / len(vals)

    if name == "COUNT":
        vals = [v for a in args for v in _flatten_arg(a, ctx)]
        return sum(1 for v in vals if isinstance(v, (int, float)) and not isinstance(v, bool))

    if name == "COUNTA":
        vals = [v for a in args for v in _flatten_arg(a, ctx)]
        return sum(1 for v in vals if v is not None and v != "")

    if name == "MIN":
        vals = [_as_number(v) for a in args for v in _flatten_arg(a, ctx) if v is not None]
        return min(vals) if vals else 0

    if name == "MAX":
        vals = [_as_number(v) for a in args for v in _flatten_arg(a, ctx) if v is not None]
        return max(vals) if vals else 0

    if name == "COUNTIF":
        _require_arg_count(name, args, 2)
        vals = _flatten_arg(args[0], ctx)
        crit = evaluate(args[1], ctx)
        return sum(1 for v in vals if _matches_criterion(v, crit))

    if name == "SUMIF":
        _require_arg_count(name, args, (2, 3))
        range_vals = _flatten_arg(args[0], ctx)
        crit = evaluate(args[1], ctx)
        sum_vals = _flatten_arg(args[2], ctx) if len(args) == 3 else range_vals
        total = 0
        for cond_v, sum_v in zip(range_vals, sum_vals):
            if _matches_criterion(cond_v, crit):
                total += _as_number(sum_v)
        return total

    if name == "SUMIFS":
        # SUMIFS(sum_range, crit_range1, crit1, crit_range2, crit2, ...)
        if len(args) < 3 or len(args) % 2 == 0:
            raise SemanticError("SUMIFS expects sum_range plus pairs of (range, criterion)")
        sum_vals = _flatten_arg(args[0], ctx)
        cond_ranges = [_flatten_arg(args[i], ctx) for i in range(1, len(args), 2)]
        criteria = [evaluate(args[i], ctx) for i in range(2, len(args), 2)]
        total = 0
        for row in range(len(sum_vals)):
            if all(_matches_criterion(cond[row], crit) for cond, crit in zip(cond_ranges, criteria)):
                total += _as_number(sum_vals[row])
        return total

    if name == "IF":
        _require_arg_count(name, args, (2, 3))
        cond = evaluate(args[0], ctx)
        if _truthy(cond):
            return evaluate(args[1], ctx)
        return evaluate(args[2], ctx) if len(args) == 3 else False

    if name == "IFS":
        if len(args) < 2 or len(args) % 2 != 0:
            raise SemanticError("IFS expects an even number of (condition, value) arguments")
        for i in range(0, len(args), 2):
            if _truthy(evaluate(args[i], ctx)):
                return evaluate(args[i + 1], ctx)
        raise SemanticError("IFS: no condition was TRUE (#N/A)")

    if name == "AND":
        return all(_truthy(evaluate(a, ctx)) for a in args)

    if name == "OR":
        return any(_truthy(evaluate(a, ctx)) for a in args)

    if name == "NOT":
        _require_arg_count(name, args, 1)
        return not _truthy(evaluate(args[0], ctx))

    if name == "IFERROR":
        _require_arg_count(name, args, 2)
        try:
            return evaluate(args[0], ctx)
        except SemanticError:
            return evaluate(args[1], ctx)

    if name == "ISERROR":
        _require_arg_count(name, args, 1)
        try:
            evaluate(args[0], ctx)
            return False
        except SemanticError:
            return True

    if name in ("CONCAT", "CONCATENATE"):
        parts = []
        for a in args:
            for v in _flatten_arg(a, ctx):
                parts.append("" if v is None else str(v))
        return "".join(parts)

    if name == "TEXT":
        _require_arg_count(name, args, 2)
        value = evaluate(args[0], ctx)
        return str(value)

    if name == "LEFT":
        _require_arg_count(name, args, (1, 2))
        s = str(evaluate(args[0], ctx))
        n = int(_as_number(evaluate(args[1], ctx))) if len(args) == 2 else 1
        return s[:n]

    if name == "RIGHT":
        _require_arg_count(name, args, (1, 2))
        s = str(evaluate(args[0], ctx))
        n = int(_as_number(evaluate(args[1], ctx))) if len(args) == 2 else 1
        return s[-n:] if n > 0 else ""

    if name == "MID":
        _require_arg_count(name, args, 3)
        s = str(evaluate(args[0], ctx))
        start = int(_as_number(evaluate(args[1], ctx)))
        length = int(_as_number(evaluate(args[2], ctx)))
        return s[start - 1: start - 1 + length]

    if name == "TRIM":
        _require_arg_count(name, args, 1)
        return re.sub(r"\s+", " ", str(evaluate(args[0], ctx))).strip()

    if name == "DATEDIF":
        _require_arg_count(name, args, 3)
        start = _to_date(evaluate(args[0], ctx))
        end = _to_date(evaluate(args[1], ctx))
        unit = str(evaluate(args[2], ctx)).upper()
        delta_days = (end - start).days
        if unit == "D":
            return delta_days
        if unit == "M":
            return (end.year - start.year) * 12 + (end.month - start.month)
        if unit == "Y":
            return end.year - start.year
        raise SemanticError(f"unsupported DATEDIF unit '{unit}'")

    if name == "VLOOKUP":
        _require_arg_count(name, args, (3, 4))
        return _vlookup(args, ctx)

    if name == "HLOOKUP":
        _require_arg_count(name, args, (3, 4))
        return _hlookup(args, ctx)

    if name == "INDEX":
        _require_arg_count(name, args, 2)
        return _index_fn(args, ctx)

    if name == "MATCH":
        _require_arg_count(name, args, (2, 3))
        return _match_fn(args, ctx)

    raise SemanticError(f"unsupported function '{name}'")


def _require_arg_count(name, args, expected):
    if isinstance(expected, tuple):
        if len(args) not in range(expected[0], expected[-1] + 1):
            raise SemanticError(f"{name} expects {expected[0]}-{expected[-1]} arguments, got {len(args)}")
    else:
        if len(args) != expected:
            raise SemanticError(f"{name} expects {expected} arguments, got {len(args)}")


def _truthy(v):
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return v != 0
    if isinstance(v, str):
        return v.upper() == "TRUE"
    return bool(v)


def _matches_criterion(value, criterion):
    """Implements Excel-style COUNTIF/SUMIF criteria: plain equality,
    or a string like '>10', '<=5', '<>0'."""
    if isinstance(criterion, str) and criterion[:2] in (">=", "<=", "<>"):
        op, rhs = criterion[:2], criterion[2:]
    elif isinstance(criterion, str) and criterion[:1] in (">", "<"):
        op, rhs = criterion[:1], criterion[1:]
    else:
        op, rhs = "=", criterion

    try:
        rhs_num = float(rhs) if not isinstance(rhs, (int, float)) else rhs
        val_num = _as_number(value)
        a, b = val_num, rhs_num
    except (SemanticError, ValueError, TypeError):
        a, b = value, rhs  # string comparison fallback

    if op == "=":
        return a == b
    if op == "<>":
        return a != b
    if op == ">":
        return a > b
    if op == "<":
        return a < b
    if op == ">=":
        return a >= b
    if op == "<=":
        return a <= b
    return False


def _to_date(v):
    if isinstance(v, date):
        return v
    if isinstance(v, (int, float)):
        # Excel's date epoch: day 1 = 1899-12-31 (with the well-known
        # 1900 leap-year quirk, ignored here for the Phase 1 subset).
        return date(1899, 12, 30) + timedelta(days=int(v))
    raise SemanticError(f"cannot interpret {v!r} as a date")


def _vlookup(args, ctx):
    lookup_value = evaluate(args[0], ctx)
    table_node = args[1]
    if not isinstance(table_node, RangeNode):
        raise SemanticError("VLOOKUP's second argument must be a range")
    col_index = int(_as_number(evaluate(args[2], ctx)))
    exact = (not _truthy(evaluate(args[3], ctx))) if len(args) == 4 else True

    cells = expand_range(_clean_ref(table_node.start_cell), _clean_ref(table_node.end_cell))
    n_cols = _CELL_RE.match(cells[-1]).group(1)
    first_col_letters = _CELL_RE.match(table_node.start_cell.replace("$", "")).group(1)
    last_col_letters = _CELL_RE.match(table_node.end_cell.replace("$", "")).group(1)
    width = _col_to_index(last_col_letters) - _col_to_index(first_col_letters) + 1

    rows = [cells[i:i + width] for i in range(0, len(cells), width)]
    for row in rows:
        row_key = ctx.get_cell_value(table_node.sheet, row[0])
        if (exact and row_key == lookup_value) or (not exact and row_key == lookup_value):
            return ctx.get_cell_value(table_node.sheet, row[col_index - 1])
    raise SemanticError(f"VLOOKUP: value {lookup_value!r} not found (#N/A)")


def _hlookup(args, ctx):
    lookup_value = evaluate(args[0], ctx)
    table_node = args[1]
    if not isinstance(table_node, RangeNode):
        raise SemanticError("HLOOKUP's second argument must be a range")
    row_index = int(_as_number(evaluate(args[2], ctx)))

    first_col_letters = _CELL_RE.match(table_node.start_cell.replace("$", "")).group(1)
    last_col_letters = _CELL_RE.match(table_node.end_cell.replace("$", "")).group(1)
    r1 = int(_CELL_RE.match(table_node.start_cell.replace("$", "")).group(2))
    r2 = int(_CELL_RE.match(table_node.end_cell.replace("$", "")).group(2))
    n_cols_list = [
        f"{_index_to_col(c)}{r1}"
        for c in range(_col_to_index(first_col_letters), _col_to_index(last_col_letters) + 1)
    ]
    for c_idx, top_cell in enumerate(n_cols_list):
        if ctx.get_cell_value(table_node.sheet, top_cell) == lookup_value:
            target_row = r1 + row_index - 1
            col_letters = _CELL_RE.match(top_cell).group(1)
            return ctx.get_cell_value(table_node.sheet, f"{col_letters}{target_row}")
    raise SemanticError(f"HLOOKUP: value {lookup_value!r} not found (#N/A)")


def _index_fn(args, ctx):
    range_node = args[0]
    if not isinstance(range_node, RangeNode):
        raise SemanticError("INDEX's first argument must be a range")
    pos = int(_as_number(evaluate(args[1], ctx)))
    values = _resolve_range_values(range_node, ctx)
    if pos < 1 or pos > len(values):
        raise SemanticError(f"INDEX: position {pos} is out of range (#REF!)")
    return values[pos - 1]


def _match_fn(args, ctx):
    lookup_value = evaluate(args[0], ctx)
    range_node = args[1]
    if not isinstance(range_node, RangeNode):
        raise SemanticError("MATCH's second argument must be a range")
    values = _resolve_range_values(range_node, ctx)
    for i, v in enumerate(values, start=1):
        if v == lookup_value:
            return i
    raise SemanticError(f"MATCH: value {lookup_value!r} not found (#N/A)")
