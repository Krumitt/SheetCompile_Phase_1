"""
main.py
=======
Wires the full Phase 1 pipeline together:

    extract -> lex -> parse -> build AST -> symbol table
             -> dependency graph -> topological sort -> evaluate

This mirrors the classic compiler front-end pipeline structure.
Run directly: `python main.py path/to/workbook.xlsx`
"""

import sys

from extractor import extract_workbook
from parser import parse_formula
from symbol_table import SymbolTable
from dependency_graph import DependencyGraph
from evaluator import EvalContext, evaluate, expand_range, _clean_ref
from ast_nodes import CellRefNode, RangeNode, NamedRangeNode, FunctionCallNode, BinaryOpNode, UnaryOpNode
from error_reporter import CompilerError, ErrorReport


def _collect_references(node, sheet, refs):
    """Walks an AST and collects every (sheet, cell) it references,
    expanding ranges into individual cells. Used to build dependency
    graph edges."""
    if isinstance(node, CellRefNode):
        refs.add((node.sheet or sheet, _clean_ref(node.cell)))
    elif isinstance(node, RangeNode):
        for c in expand_range(_clean_ref(node.start_cell), _clean_ref(node.end_cell)):
            refs.add((node.sheet or sheet, c))
    elif isinstance(node, NamedRangeNode):
        pass  # resolved separately via the symbol table, see run_pipeline()
    elif isinstance(node, BinaryOpNode):
        _collect_references(node.left, sheet, refs)
        _collect_references(node.right, sheet, refs)
    elif isinstance(node, UnaryOpNode):
        _collect_references(node.operand, sheet, refs)
    elif isinstance(node, FunctionCallNode):
        for arg in node.args:
            _collect_references(arg, sheet, refs)


def run_pipeline(path, verbose=True):
    """Runs the full Phase 1 pipeline on a workbook file and returns
    (computed_values, error_report). computed_values maps
    (sheet, cell) -> evaluated value for every cell in the workbook."""
    errors = ErrorReport()
    wb = extract_workbook(path)

    symtab = SymbolTable()
    for c in wb.all_cells():
        symtab.insert_cell(c.sheet, c.cell, c.is_formula, c.raw_value)
    for name, (sheet, ref) in wb.named_ranges.items():
        symtab.insert_named_range(name, sheet, ref)

    # --- lex + parse every formula cell, building the dependency graph ---
    asts = {}                 # (sheet, cell) -> AST root
    graph = DependencyGraph()

    for c in wb.all_cells():
        graph.add_node((c.sheet, c.cell))
        if not c.is_formula:
            continue
        try:
            ast_root = parse_formula(c.raw_value, sheet=c.sheet, cell=c.cell)
            asts[(c.sheet, c.cell)] = ast_root
            refs = set()
            _collect_references(ast_root, c.sheet, refs)
            for dep in refs:
                graph.add_dependency((c.sheet, c.cell), dep)
        except CompilerError as e:
            errors.add(e)

    if errors.has_errors():
        if verbose:
            errors.print_all()
        return None, errors

    # --- topological order (raises DependencyError on a cycle) -----------
    try:
        order = graph.topological_order()
    except CompilerError as e:
        errors.add(e)
        if verbose:
            errors.print_all()
        return None, errors

    # --- evaluate every cell in dependency order ---------------------------
    computed_values = {}
    for sheet, cell in order:
        entry = wb.get_cell(sheet, cell)
        if entry is None:
            continue  # a referenced cell that was actually blank
        if not entry.is_formula:
            computed_values[(sheet, cell)] = entry.raw_value
            continue
        ast_root = asts.get((sheet, cell))
        ctx = EvalContext(current_sheet=sheet, symbol_table=symtab, computed_values=computed_values)
        try:
            computed_values[(sheet, cell)] = evaluate(ast_root, ctx)
        except CompilerError as e:
            e.sheet, e.cell = sheet, cell
            errors.add(e)
            computed_values[(sheet, cell)] = None

    if verbose and errors.has_errors():
        errors.print_all()

    return computed_values, errors


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python main.py path/to/workbook.xlsx")
        sys.exit(1)

    values, errs = run_pipeline(sys.argv[1])
    if values:
        for (sheet, cell), val in sorted(values.items()):
            print(f"{sheet}!{cell} = {val!r}")
    if errs.has_errors():
        sys.exit(1)
