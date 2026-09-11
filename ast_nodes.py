"""
ast_nodes.py
============
Compiler-design concept: ABSTRACT SYNTAX TREE REPRESENTATION.

Each class here is one kind of node the parser can produce. Keeping
these as small, dedicated classes (rather than generic tuples/dicts)
makes the evaluator's tree-walk (evaluator.py) read like a direct
implementation of the grammar in language_spec.py: one visit_* case
per node type.
"""


class ASTNode:
    """Base class. Every node knows how to pretty_print() itself as an
    indented text tree, which is what Review 1's 'AST visualization'
    deliverable uses."""

    def pretty_print(self, indent=0):
        raise NotImplementedError


class NumberNode(ASTNode):
    def __init__(self, value):
        self.value = value

    def pretty_print(self, indent=0):
        return "  " * indent + f"Number({self.value})"


class BooleanNode(ASTNode):
    def __init__(self, value):
        self.value = value  # True or False

    def pretty_print(self, indent=0):
        return "  " * indent + f"Boolean({self.value})"


class StringNode(ASTNode):
    def __init__(self, value):
        self.value = value

    def pretty_print(self, indent=0):
        return "  " * indent + f'String("{self.value}")'


class CellRefNode(ASTNode):
    """A single cell reference, e.g. A1, $A$1, or Sheet2!B3."""

    def __init__(self, cell, sheet=None):
        self.cell = cell        # e.g. "A1" or "$A$1"
        self.sheet = sheet      # None means "current sheet"

    def pretty_print(self, indent=0):
        label = f"{self.sheet}!{self.cell}" if self.sheet else self.cell
        return "  " * indent + f"CellRef({label})"


class RangeNode(ASTNode):
    """A range of cells, e.g. A1:B10 or Sheet2!A1:A5."""

    def __init__(self, start_cell, end_cell, sheet=None):
        self.start_cell = start_cell
        self.end_cell = end_cell
        self.sheet = sheet

    def pretty_print(self, indent=0):
        label = f"{self.start_cell}:{self.end_cell}"
        if self.sheet:
            label = f"{self.sheet}!{label}"
        return "  " * indent + f"Range({label})"


class NamedRangeNode(ASTNode):
    """An identifier used as a named range (not a function call),
    e.g. TaxRate."""

    def __init__(self, name):
        self.name = name

    def pretty_print(self, indent=0):
        return "  " * indent + f"NamedRange({self.name})"


class UnaryOpNode(ASTNode):
    def __init__(self, op, operand):
        self.op = op            # e.g. "-"
        self.operand = operand

    def pretty_print(self, indent=0):
        pad = "  " * indent
        return pad + f"UnaryOp({self.op})\n" + self.operand.pretty_print(indent + 1)


class BinaryOpNode(ASTNode):
    def __init__(self, op, left, right):
        self.op = op            # e.g. "+", "*", "=", "<>"
        self.left = left
        self.right = right

    def pretty_print(self, indent=0):
        pad = "  " * indent
        out = pad + f"BinaryOp({self.op})\n"
        out += self.left.pretty_print(indent + 1) + "\n"
        out += self.right.pretty_print(indent + 1)
        return out


class FunctionCallNode(ASTNode):
    def __init__(self, name, args):
        self.name = name        # e.g. "SUM"
        self.args = args        # list[ASTNode]

    def pretty_print(self, indent=0):
        pad = "  " * indent
        out = pad + f"FunctionCall({self.name})\n"
        arg_lines = [a.pretty_print(indent + 1) for a in self.args]
        out += "\n".join(arg_lines)
        return out
