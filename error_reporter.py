"""
error_reporter.py
==================
Compiler-design concept: ERROR DETECTION AND REPORTING.

A real compiler never just crashes with a raw traceback — it reports
*where* the problem is (file/line/column, or here: sheet/cell/position)
and *what* was expected. This module defines that shared diagnostic
format and the exception hierarchy used by every other stage
(lexer, parser, evaluator).
"""


class CompilerError(Exception):
    """Base class for every diagnostic SheetCompile can raise.

    Storing sheet/cell/position/message separately (instead of just a
    formatted string) lets the test suite and the CLI decide how to
    display the error, while __str__ gives a sensible default.
    """

    def __init__(self, message, sheet=None, cell=None, position=None):
        self.message = message
        self.sheet = sheet
        self.cell = cell
        self.position = position
        super().__init__(self.format())

    def format(self):
        location = ""
        if self.sheet and self.cell:
            location = f"{self.sheet}!{self.cell}"
        elif self.cell:
            location = self.cell

        if location and self.position is not None:
            return f"Error at {location}, position {self.position}: {self.message}"
        elif location:
            return f"Error at {location}: {self.message}"
        else:
            return f"Error: {self.message}"

    def __str__(self):
        return self.format()


class LexError(CompilerError):
    """Raised by lexer.py for illegal characters, malformed numbers,
    or unterminated strings."""
    pass


class ParseError(CompilerError):
    """Raised by parser.py when the token stream does not match the
    grammar in language_spec.py (e.g. missing closing paren)."""
    pass


class SemanticError(CompilerError):
    """Raised by evaluator.py for type errors, e.g. SUM() over text,
    or wrong argument counts caught before/at evaluation time."""
    pass


class DependencyError(CompilerError):
    """Raised by dependency_graph.py when a circular reference is
    detected between cells."""
    pass


class ErrorReport:
    """Accumulates errors across an entire pipeline run so the CLI /
    test suite can print a full diagnostic summary instead of stopping
    at the first failure (this mirrors how real compilers batch-report
    errors instead of halting on error #1)."""

    def __init__(self):
        self.errors = []

    def add(self, error: CompilerError):
        self.errors.append(error)

    def has_errors(self):
        return len(self.errors) > 0

    def print_all(self):
        for err in self.errors:
            print(str(err))

    def __len__(self):
        return len(self.errors)
