"""
lexer.py
========
Compiler-design concept: LEXICAL ANALYSIS.

Converts a raw Excel formula string (e.g. "=SUM(A1:A10)*2") into a flat
stream of Token objects. This is a hand-written scanning-loop lexer
(no regex-engine-as-a-crutch for the overall scan) so every character
decision is explicit and explainable in a viva.

The lexer does NOT know about grammar/precedence — that is the parser's
job. It only classifies characters into tokens and reports lexical
errors (illegal characters, malformed numbers, unterminated strings)
with the exact character position, which the error_reporter module
then formats consistently.
"""

from language_spec import (
    TT_NUMBER, TT_STRING, TT_CELL_REF, TT_IDENTIFIER, TT_EOF,
    TWO_CHAR_OPERATORS, ONE_CHAR_OPERATORS,
)
from error_reporter import LexError


class Token:
    """A single lexical unit: its type, the literal text it came from,
    and the character position it started at (for error messages)."""

    def __init__(self, type_, value, position):
        self.type = type_
        self.value = value
        self.position = position

    def __repr__(self):
        return f"Token({self.type}, {self.value!r}, pos={self.position})"


def _is_cell_ref_shape(word):
    """A CELL_REF looks like an optional '$', one or more letters,
    an optional '$', then one or more digits — e.g. A1, $A$1, B$12.
    We check this shape *after* scanning a generic identifier-like
    word, rather than baking it into the character-scanning loop,
    which keeps the scanner itself simple.
    """
    i = 0
    n = len(word)
    if i < n and word[i] == "$":
        i += 1
    start_letters = i
    while i < n and word[i].isalpha():
        i += 1
    if i == start_letters:
        return False  # no letters at all
    if i < n and word[i] == "$":
        i += 1
    start_digits = i
    while i < n and word[i].isdigit():
        i += 1
    if i == start_digits:
        return False  # no digits at all
    return i == n  # the whole word must be consumed


class Lexer:
    """Hand-written scanning-loop lexer for the SheetCompile formula
    subset defined in language_spec.py."""

    def __init__(self, text, sheet=None, cell=None):
        # Strip a single leading '=' if present; Excel formulas are
        # always written with one, but it carries no grammatical
        # meaning beyond "this cell contains a formula".
        self.text = text[1:] if text.startswith("=") else text
        self.pos = 0
        self.sheet = sheet   # only used for error messages
        self.cell = cell     # only used for error messages

    def _peek(self, offset=0):
        p = self.pos + offset
        return self.text[p] if p < len(self.text) else ""

    def _advance(self):
        ch = self.text[self.pos]
        self.pos += 1
        return ch

    def tokenize(self):
        tokens = []
        while self.pos < len(self.text):
            ch = self._peek()

            if ch.isspace():
                self.pos += 1
                continue

            if ch == '"':
                tokens.append(self._read_string())
                continue

            if ch.isdigit() or (ch == "." and self._peek(1).isdigit()):
                tokens.append(self._read_number())
                continue

            if ch.isalpha() or ch == "_":
                tokens.append(self._read_word())
                continue

            two = self.text[self.pos:self.pos + 2]
            if two in TWO_CHAR_OPERATORS:
                start = self.pos
                self.pos += 2
                tokens.append(Token(TWO_CHAR_OPERATORS[two], two, start))
                continue

            if ch in ONE_CHAR_OPERATORS:
                start = self.pos
                self._advance()
                tokens.append(Token(ONE_CHAR_OPERATORS[ch], ch, start))
                continue

            raise LexError(
                f"illegal character {ch!r}",
                sheet=self.sheet, cell=self.cell, position=self.pos,
            )

        tokens.append(Token(TT_EOF, None, self.pos))
        return tokens

    def _read_string(self):
        start = self.pos
        self._advance()  # consume opening quote
        chars = []
        while True:
            if self.pos >= len(self.text):
                raise LexError(
                    "unterminated string literal",
                    sheet=self.sheet, cell=self.cell, position=start,
                )
            ch = self._advance()
            if ch == '"':
                # Excel escapes a literal quote as "" inside a string.
                if self._peek() == '"':
                    chars.append('"')
                    self._advance()
                    continue
                break
            chars.append(ch)
        return Token(TT_STRING, "".join(chars), start)

    def _read_number(self):
        start = self.pos
        chars = []
        seen_dot = False
        while self.pos < len(self.text) and (self._peek().isdigit() or self._peek() == "."):
            if self._peek() == ".":
                if seen_dot:
                    raise LexError(
                        "malformed number literal (multiple decimal points)",
                        sheet=self.sheet, cell=self.cell, position=start,
                    )
                seen_dot = True
            chars.append(self._advance())
        return Token(TT_NUMBER, float("".join(chars)), start)

    def _read_word(self):
        """Reads a run of letters/digits/underscores. Afterwards, decides
        whether it 'looks like' a cell reference (A1, $A$1, ...) or is a
        general identifier (function name or named range). This
        classification is a lexical decision here; the parser later
        decides, from *context*, whether an identifier is a function
        call or a named range.
        """
        start = self.pos
        chars = []
        if self._peek() == "$":
            chars.append(self._advance())
        while self.pos < len(self.text) and (self._peek().isalnum() or self._peek() == "_"):
            chars.append(self._advance())
        # allow a second '$' for mixed/absolute refs like A$1 or $A$1
        if self._peek() == "$":
            chars.append(self._advance())
            while self.pos < len(self.text) and self._peek().isdigit():
                chars.append(self._advance())

        word = "".join(chars)
        if _is_cell_ref_shape(word):
            return Token(TT_CELL_REF, word.upper(), start)
        return Token(TT_IDENTIFIER, word.upper(), start)
