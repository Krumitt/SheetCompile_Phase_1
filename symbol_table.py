"""
symbol_table.py
================
Compiler-design concept: SYMBOL TABLE MANAGEMENT.

Tracks every "identifier" a workbook's formulas can refer to:
  - individual cell references, per sheet
  - named ranges, and what cell/range they resolve to
  - cross-sheet references

In a traditional compiler the symbol table maps variable names to
type/scope/memory-location info. Here the analogous mapping is
cell-reference -> "is it a literal or a formula, and what sheet is it
on", plus named-range -> "what does this name actually point to".
"""


class Symbol:
    """One entry in the symbol table: a single cell."""

    def __init__(self, sheet, cell, is_formula, raw_value):
        self.sheet = sheet
        self.cell = cell
        self.is_formula = is_formula   # True if this cell holds a formula
        self.raw_value = raw_value     # formula string OR literal value

    def key(self):
        return (self.sheet, self.cell)

    def __repr__(self):
        kind = "formula" if self.is_formula else "literal"
        return f"Symbol({self.sheet}!{self.cell}, {kind}={self.raw_value!r})"


class SymbolTable:
    def __init__(self):
        self._cells = {}          # (sheet, cell) -> Symbol
        self._named_ranges = {}   # name -> (sheet, cell_or_range_string)

    # ---- cell symbols -------------------------------------------------
    def insert_cell(self, sheet, cell, is_formula, raw_value):
        sym = Symbol(sheet, cell, is_formula, raw_value)
        self._cells[sym.key()] = sym
        return sym

    def lookup_cell(self, sheet, cell):
        return self._cells.get((sheet, cell))

    def all_cells(self):
        return list(self._cells.values())

    # ---- named ranges ---------------------------------------------------
    def insert_named_range(self, name, sheet, ref):
        """`ref` is the cell or range string the name resolves to,
        e.g. 'A1' or 'A1:A10'."""
        self._named_ranges[name.upper()] = (sheet, ref)

    def lookup_named_range(self, name):
        return self._named_ranges.get(name.upper())

    def all_named_ranges(self):
        return dict(self._named_ranges)

    def __repr__(self):
        return (f"SymbolTable({len(self._cells)} cells, "
                f"{len(self._named_ranges)} named ranges)")
