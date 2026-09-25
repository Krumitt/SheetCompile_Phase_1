"""
ir_generator.py
================
Compiler-design concept: INTERMEDIATE CODE GENERATION.

This is Sub-Phase 2 of the project. It sits between the two passes you
already have:

    lexer -> parser -> AST -> [semantic_analyzer.py]  (Sub-Phase 1)
                            -> [ir_generator.py]        (Sub-Phase 2, this file)
                            -> target code (pandas)     (Sub-Phase 3, later)

A real compiler does not translate the AST straight into target code.
It first lowers the AST into a machine-independent INTERMEDIATE
REPRESENTATION (IR) -- classically THREE-ADDRESS CODE (TAC), where
every instruction has at most one operator and at most two operands,
plus a place to store the result:

        result = arg1 op arg2

This module implements that lowering for a single Excel-formula AST,
using the QUADRUPLE representation (op, arg1, arg2, result) -- the
same representation taught for TAC in standard compiler-design courses
(Aho/Ullman "the Dragon Book"). Each quadruple is one array/list entry,
which is exactly what Requirement 2 ("structured list of instructions")
asks for.

Design mirrors semantic_analyzer.py on purpose, for consistency you can
defend in a viva:

  - IRGenerator is a VISITOR over ast_nodes.py's node classes, one
    visit_<NodeType> method per class, dispatched by a table keyed on
    type(node) -- exactly like SemanticAnalyzer.
  - It optionally takes the Sub-Phase 1 TypedSymbolTable/SymbolTable so
    named ranges (e.g. TaxRate) are lowered to the concrete cell/range
    they resolve to, instead of staying an opaque name -- reusing the
    symbol table you already built rather than re-deriving that
    information.
  - generate_workbook_ir() is the driver, mirroring
    semantic_analyzer.analyze_workbook(): it walks every formula cell
    in the same dependency order main.py already computes.

Key translation rules (the "textbook" part):

  - Literals (Number/String/Boolean) and references (CellRef/Range/
    NamedRange) are NOT computations, so -- like a variable name `x`
    feeding into `t1 = x + y` in a normal TAC scheme -- they are
    returned as plain operands and do not, by themselves, emit an
    instruction.
  - UnaryOpNode('-', operand)      -> t_new = UMINUS operand
  - BinaryOpNode(op, left, right)  -> t_new = left op right
  - FunctionCallNode(name, args)   -> one PARAM instruction per
    argument (evaluated left-to-right, exactly like C-style TAC calling
    conventions), followed by t_new = CALL name, argcount
  - Every cell's formula finishes with one ASSIGN quadruple that ties
    the last temporary back to the cell itself:
        A1 = t3
    which is the bridge Sub-Phase 3's target-code generator will hang
    the `df['A1'] = ...` pandas assignment off of.

Temporaries (t1, t2, t3, ...) are numbered by a single counter shared
across the whole IRGenerator instance (not reset per cell), exactly
like a real compiler numbers temporaries uniquely within one
translation unit.
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
from evaluator import _clean_ref  # same '$'-stripping helper main.py already reuses


# ---------------------------------------------------------------------------
# The IR instruction: a quadruple (op, arg1, arg2, result)
# ---------------------------------------------------------------------------
class Quadruple:
    """One three-address-code instruction: result = arg1 op arg2 (or a
    variant for the unary/PARAM/CALL/ASSIGN op-codes below).

    Kept as a small dedicated class -- not a bare tuple -- so it prints
    itself two ways: format_quad() for the raw (op, arg1, arg2, result)
    table view, and format_tac() for the readable 't1 = A1 + A2' view.
    """

    __slots__ = ("op", "arg1", "arg2", "result")

    # op-codes that are not literal Excel operators
    ASSIGN = "ASSIGN"  # result = arg1
    UMINUS = "UMINUS"  # result = -arg1
    PARAM = "PARAM"  # push arg1 as the next call argument
    CALL = "CALL"  # result = CALL arg1(name), arg2(argcount)

    def __init__(self, op, arg1, arg2, result):
        self.op = op
        self.arg1 = arg1
        self.arg2 = arg2
        self.result = result

    # ---- two printing styles ---------------------------------------
    def format_quad(self):
        """The raw quadruple, (op, arg1, arg2, result) -- the classic
        compiler-design-textbook table form."""
        fmt = lambda v: "-" if v is None else v
        return f"({self.op}, {fmt(self.arg1)}, {fmt(self.arg2)}, {fmt(self.result)})"

    def format_tac(self):
        """Readable three-address-code text, e.g. 't1 = A1 + A2'."""
        if self.op == Quadruple.ASSIGN:
            return f"{self.result} = {self.arg1}"
        if self.op == Quadruple.UMINUS:
            return f"{self.result} = -{self.arg1}"
        if self.op == Quadruple.PARAM:
            return f"PARAM {self.arg1}"
        if self.op == Quadruple.CALL:
            return f"{self.result} = CALL {self.arg1}, {self.arg2}"
        # binary Excel operators (+ - * / ^ & = <> < > <= >=) fall
        # straight through as infix, which is the whole point of TAC
        return f"{self.result} = {self.arg1} {self.op} {self.arg2}"

    def __repr__(self):
        return f"Quadruple{(self.op, self.arg1, self.arg2, self.result)!r}"

    def __eq__(self, other):
        return isinstance(other, Quadruple) and (
            self.op,
            self.arg1,
            self.arg2,
            self.result,
        ) == (other.op, other.arg1, other.arg2, other.result)


# ---------------------------------------------------------------------------
# The IR generator: a visitor over ast_nodes.py, AST -> list[Quadruple]
# ---------------------------------------------------------------------------
class IRGenerator:
    """Lowers one cell's AST into a list of Quadruple instructions.

    symbol_table is optional (a symbol_table.SymbolTable or
    semantic_analyzer.TypedSymbolTable). When given, NamedRangeNode is
    resolved to the concrete cell/range it points to, so the IR never
    carries a name the target-code generator would have to re-resolve.
    Without it, the named range's own identifier is kept as a symbolic
    operand.
    """

    def __init__(self, symbol_table=None):
        self.symtab = symbol_table
        self.instructions = []  # list[Quadruple], the full program so far
        self._temp_count = 0
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

    # ---- temporaries --------------------------------------------------
    def new_temp(self):
        self._temp_count += 1
        return f"t{self._temp_count}"

    def emit(self, op, arg1, arg2, result):
        quad = Quadruple(op, arg1, arg2, result)
        self.instructions.append(quad)
        return quad

    # ---- public entry point --------------------------------------------
    def generate_cell(self, ast_root, sheet, cell):
        """Lowers one formula cell's AST to TAC and appends it to
        self.instructions. Returns just that cell's slice of
        instructions (including the trailing ASSIGN), which is what
        gets stored per-cell by generate_workbook_ir() below."""
        self.current_sheet = sheet
        self.current_cell = cell
        start = len(self.instructions)

        operand = self.visit(ast_root)

        target = self._cell_label(sheet, cell)
        # Tie the final computed operand back to the cell it belongs
        # to -- this ASSIGN is the hook Sub-Phase 3 (target-code gen)
        # will translate into `df.loc[:, 'A1'] = ...`.
        self.emit(Quadruple.ASSIGN, operand, None, target)

        return self.instructions[start:]

    # ---- dispatch -------------------------------------------------------
    def visit(self, node):
        handler = self._dispatch.get(type(node))
        if handler is None:
            raise TypeError(
                f"ir_generator: no lowering rule for AST node {type(node).__name__}"
            )
        return handler(node)

    # ---- label helpers (sheet-qualify only when needed) ------------------
    def _cell_label(self, sheet, cell):
        cell = _clean_ref(cell)
        if sheet and sheet != self.current_sheet:
            return f"{sheet}!{cell}"
        return cell

    def _range_label(self, sheet, start_cell, end_cell):
        start_cell, end_cell = _clean_ref(start_cell), _clean_ref(end_cell)
        label = f"{start_cell}:{end_cell}"
        if sheet and sheet != self.current_sheet:
            return f"{sheet}!{label}"
        return label

    # ---- literals: plain operands, no instruction emitted -----------------
    def visit_number(self, node: NumberNode):
        return node.value

    def visit_string(self, node: StringNode):
        return f'"{node.value}"'

    def visit_boolean(self, node: BooleanNode):
        return "TRUE" if node.value else "FALSE"

    # ---- references: plain operands, no instruction emitted ----------------
    def visit_cell_ref(self, node: CellRefNode):
        return self._cell_label(node.sheet or self.current_sheet, node.cell)

    def visit_range(self, node: RangeNode):
        return self._range_label(
            node.sheet or self.current_sheet, node.start_cell, node.end_cell
        )

    def visit_named_range(self, node: NamedRangeNode):
        """Resolves through the Sub-Phase 1 symbol table when one was
        supplied; otherwise keeps the name itself as a symbolic
        operand (still valid TAC -- just not yet address-bound)."""
        if self.symtab is not None:
            resolved = self.symtab.lookup_named_range(node.name)
            if resolved is not None:
                sheet, ref = resolved
                if ":" in ref:
                    start, end = ref.split(":")
                    return self._range_label(sheet, start, end)
                return self._cell_label(sheet, ref)
        return node.name

    # ---- operators: these are the ones that actually emit TAC -------------
    def visit_unary_op(self, node: UnaryOpNode):
        operand = self.visit(node.operand)
        if node.op == "-":
            temp = self.new_temp()
            self.emit(Quadruple.UMINUS, operand, None, temp)
            return temp
        raise ValueError(f"ir_generator: unsupported unary operator {node.op!r}")

    def visit_binary_op(self, node: BinaryOpNode):
        left = self.visit(node.left)
        right = self.visit(node.right)
        temp = self.new_temp()
        # node.op is already one of + - * / ^ & = <> < > <= >=, and is
        # used verbatim as the quadruple's op-code -- this is exactly
        # the "one operator per instruction" rule TAC is named for.
        self.emit(node.op, left, right, temp)
        return temp

    def visit_function_call(self, node: FunctionCallNode):
        # Evaluate/lower every argument first (left to right), each
        # becoming its own PARAM instruction -- the standard TAC
        # calling convention for functions of arbitrary arity, which a
        # flat (op, arg1, arg2, result) triple cannot express directly.
        arg_operands = [self.visit(arg) for arg in node.args]
        for arg_operand in arg_operands:
            self.emit(Quadruple.PARAM, arg_operand, None, None)

        temp = self.new_temp()
        self.emit(Quadruple.CALL, node.name.upper(), len(arg_operands), temp)
        return temp


# ---------------------------------------------------------------------------
# Printing: satisfies the "Intermediate results" deliverable
# ---------------------------------------------------------------------------
def print_ir(instructions, title=None):
    """Clean, human-readable dump of a list[Quadruple]: an index
    column, the readable TAC line, and the raw quadruple next to it so
    both representations required by the rubric are visible at once."""
    if title:
        print(f"--- {title} ---")
    if not instructions:
        print("(no instructions)")
        return
    width = max(len(instr.format_tac()) for instr in instructions)
    for i, instr in enumerate(instructions, start=1):
        print(f"{i:>3}: {instr.format_tac():<{width}}   {instr.format_quad()}")


def print_workbook_ir(cell_instructions):
    """cell_instructions: dict[(sheet, cell) -> list[Quadruple]], as
    returned by generate_workbook_ir(). Prints one labelled block per
    cell, in the order the dict was populated (i.e. dependency order)."""
    for (sheet, cell), instrs in cell_instructions.items():
        print_ir(instrs, title=f"{sheet}!{cell}")
        print()


# ---------------------------------------------------------------------------
# Driver: generate IR for every formula cell in dependency order
# ---------------------------------------------------------------------------
def generate_workbook_ir(asts, order, symtab=None):
    """Mirrors semantic_analyzer.analyze_workbook()'s shape, but for IR
    generation instead of type-checking.

    asts    - dict[(sheet, cell) -> AST root], already parsed (main.py
              already builds this)
    order   - topological order from DependencyGraph.topological_order()
              (only formula cells matter here; literal cells are
              skipped since they have no formula to lower)
    symtab  - optional SymbolTable/TypedSymbolTable, used to resolve
              named ranges (see IRGenerator.visit_named_range)

    Returns (generator, cell_instructions):
      generator          - the IRGenerator instance (generator.instructions
                            is the full flat program, in emission order)
      cell_instructions  - dict[(sheet, cell) -> list[Quadruple]], that
                            cell's own slice of the program
    """
    generator = IRGenerator(symbol_table=symtab)
    cell_instructions = {}

    for sheet, cell in order:
        ast_root = asts.get((sheet, cell))
        if ast_root is None:
            continue  # not a formula cell (or it failed parsing earlier)
        cell_instructions[(sheet, cell)] = generator.generate_cell(
            ast_root, sheet, cell
        )

    return generator, cell_instructions


# ---------------------------------------------------------------------------
# Self-test: run directly with `python ir_generator.py` to sanity-check
# this module in isolation, without needing a real .xlsx workbook.
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    from parser import parse_formula
    from symbol_table import SymbolTable

    PASS, FAIL = "PASS", "FAIL"
    _results = []

    def _check(label, fn):
        try:
            fn()
            _results.append((label, PASS, ""))
        except AssertionError as e:
            _results.append((label, FAIL, str(e)))
        except (
            Exception
        ) as e:  # noqa: BLE001 - report any failure, don't crash the suite
            _results.append((label, FAIL, f"{type(e).__name__}: {e}"))

    def _generate(formula, sheet="Sheet1", cell="Z1", symtab=None):
        ast_root = parse_formula(formula, sheet=sheet, cell=cell)
        gen = IRGenerator(symbol_table=symtab)
        instrs = gen.generate_cell(ast_root, sheet, cell)
        return gen, instrs

    def _build_symtab():
        st = SymbolTable()
        st.insert_cell("Sheet1", "A1", is_formula=False, raw_value=10)
        st.insert_named_range("TaxRate", "Sheet1", "A1")
        return st

    # ---- demo: print IR for a handful of representative formulas ----------
    print("=" * 70)
    print("ir_generator.py -- sample Three-Address Code output")
    print("=" * 70)
    demo_formulas = [
        "=A1+A2",
        "=(A1+A2)*B1",
        "=-A1",
        "=SUM(A1:A10)",
        '=IF(A1>5,"big","small")',
        "=VLOOKUP(A1,B1:C10,2,FALSE)",
    ]
    for formula in demo_formulas:
        gen, instrs = _generate(formula)
        print_ir(instrs, title=f"Sheet1!Z1  {formula}")
        print()

    # ---- correctness checks -------------------------------------------
    def test_binary_op_emits_one_instruction_plus_assign():
        gen, instrs = _generate("=A1+A2")
        assert (
            len(instrs) == 2
        ), f"expected 2 quadruples (+ and ASSIGN), got {len(instrs)}"
        assert instrs[0].op == "+", f"expected '+' op-code, got {instrs[0].op}"
        assert instrs[0].arg1 == "A1" and instrs[0].arg2 == "A2"
        assert instrs[1].op == Quadruple.ASSIGN and instrs[1].result == "Z1"

    def test_temps_are_unique_and_increasing():
        gen, instrs = _generate("=(A1+A2)*B1")
        temps = [i.result for i in instrs if i.result and i.result.startswith("t")]
        assert temps == sorted(
            set(temps), key=lambda t: int(t[1:])
        ), "temp names are not strictly increasing"
        assert len(set(temps)) == len(temps), "temp names are not unique"

    def test_operator_precedence_reflected_in_temp_order():
        # (A1+A2)*B1 must compute the '+' before the '*'
        gen, instrs = _generate("=(A1+A2)*B1")
        ops = [i.op for i in instrs]
        assert ops.index("+") < ops.index(
            "*"
        ), "addition must be lowered before multiplication"

    def test_unary_minus_uses_uminus_opcode():
        gen, instrs = _generate("=-A1")
        assert instrs[0].op == Quadruple.UMINUS
        assert instrs[0].arg1 == "A1"

    def test_function_call_uses_param_then_call():
        gen, instrs = _generate("=SUM(A1:A10)")
        assert (
            instrs[0].op == Quadruple.PARAM
        ), "expected a PARAM instruction before CALL"
        assert instrs[0].arg1 == "A1:A10"
        assert (
            instrs[1].op == Quadruple.CALL
            and instrs[1].arg1 == "SUM"
            and instrs[1].arg2 == 1
        )

    def test_multi_arg_call_emits_one_param_per_argument():
        gen, instrs = _generate("=VLOOKUP(A1,B1:C10,2,FALSE)")
        param_instrs = [i for i in instrs if i.op == Quadruple.PARAM]
        assert (
            len(param_instrs) == 4
        ), f"expected 4 PARAM instructions, got {len(param_instrs)}"
        call_instrs = [i for i in instrs if i.op == Quadruple.CALL]
        assert call_instrs[0].arg1 == "VLOOKUP" and call_instrs[0].arg2 == 4

    def test_if_branches_lower_as_call_arguments():
        gen, instrs = _generate('=IF(A1>5,"big","small")')
        assert any(
            i.op == ">" for i in instrs
        ), "condition A1>5 should lower to a '>' quadruple"
        call_instrs = [i for i in instrs if i.op == Quadruple.CALL]
        assert call_instrs[0].arg1 == "IF" and call_instrs[0].arg2 == 3

    def test_final_instruction_assigns_back_to_cell():
        gen, instrs = _generate("=A1+A2", cell="C5")
        assert instrs[-1].op == Quadruple.ASSIGN and instrs[-1].result == "C5"

    def test_named_range_resolves_via_symbol_table():
        gen, instrs = _generate("=TaxRate*2", symtab=_build_symtab())
        assert (
            instrs[0].arg1 == "A1"
        ), f"expected TaxRate to resolve to A1, got {instrs[0].arg1}"

    def test_named_range_without_symtab_keeps_symbolic_name():
        # The lexer normalizes identifiers to upper case (lexer.py),
        # same as TaxRate resolving as TAXRATE everywhere else in the
        # pipeline -- so the *symbolic* (unresolved) operand is TAXRATE.
        gen, instrs = _generate("=TaxRate*2", symtab=None)
        assert instrs[0].arg1 == "TAXRATE"

    def test_temp_counter_shared_across_cells_in_one_generator():
        gen = IRGenerator()
        i1 = gen.generate_cell(
            parse_formula("=A1+A2", sheet="Sheet1", cell="C1"), "Sheet1", "C1"
        )
        i2 = gen.generate_cell(
            parse_formula("=A1+A2", sheet="Sheet1", cell="C2"), "Sheet1", "C2"
        )
        t1 = i1[0].result
        t2 = i2[0].result
        assert (
            t1 != t2
        ), "temporaries must not be reused across cells in the same generator"

    def test_quadruple_and_tac_string_forms():
        gen, instrs = _generate("=A1+A2")
        assert instrs[0].format_tac() == "t1 = A1 + A2"
        assert instrs[0].format_quad() == "(+, A1, A2, t1)"

    _check(
        "binary op -> one op quadruple + ASSIGN",
        test_binary_op_emits_one_instruction_plus_assign,
    )
    _check(
        "temporaries are unique and increasing", test_temps_are_unique_and_increasing
    )
    _check(
        "precedence reflected in instruction order",
        test_operator_precedence_reflected_in_temp_order,
    )
    _check("unary '-' uses UMINUS op-code", test_unary_minus_uses_uminus_opcode)
    _check(
        "function call -> PARAM(s) then CALL", test_function_call_uses_param_then_call
    )
    _check(
        "multi-arg call emits one PARAM per argument",
        test_multi_arg_call_emits_one_param_per_argument,
    )
    _check(
        "IF() branches lower as CALL arguments",
        test_if_branches_lower_as_call_arguments,
    )
    _check(
        "final instruction ASSIGNs back to the cell",
        test_final_instruction_assigns_back_to_cell,
    )
    _check(
        "named range resolves via symbol table",
        test_named_range_resolves_via_symbol_table,
    )
    _check(
        "named range w/o symtab keeps symbolic name",
        test_named_range_without_symtab_keeps_symbolic_name,
    )
    _check(
        "temp counter is shared across cells",
        test_temp_counter_shared_across_cells_in_one_generator,
    )
    _check("quadruple + TAC string formatting", test_quadruple_and_tac_string_forms)

    print("=" * 70)
    print("ir_generator.py self-test")
    print("=" * 70)
    for label, status, detail in _results:
        tag = (
            f"\033[92m{status}\033[0m" if status == PASS else f"\033[91m{status}\033[0m"
        )
        print(f"[{tag}] {label}" + (f"  -> {detail}" if detail else ""))

    n_fail = sum(1 for _, status, _ in _results if status == FAIL)
    print("-" * 70)
    print(f"{len(_results) - n_fail}/{len(_results)} checks passed")
    if n_fail:
        raise SystemExit(1)
