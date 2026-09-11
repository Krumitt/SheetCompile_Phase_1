"""
parser.py
=========
Compiler-design concept: SYNTAX ANALYSIS (PARSING).

Hand-written recursive-descent parser implementing the grammar
documented in language_spec.py. Each grammar rule
(comparison, additive, term, power, primary, ...) becomes one method,
so the code is a direct, line-by-line mirror of the EBNF — this is
what makes it defensible in a viva: there is no generated parser
table to hide behind.
"""

from language_spec import (
    TT_NUMBER, TT_STRING, TT_CELL_REF, TT_IDENTIFIER, TT_EOF,
    TT_PLUS, TT_MINUS, TT_STAR, TT_SLASH, TT_CARET, TT_AMP,
    TT_EQ, TT_NEQ, TT_LE, TT_GE, TT_LT, TT_GT,
    TT_LPAREN, TT_RPAREN, TT_COMMA, TT_COLON, TT_BANG,
)
from ast_nodes import (
    NumberNode, StringNode, BooleanNode, CellRefNode, RangeNode, NamedRangeNode,
    UnaryOpNode, BinaryOpNode, FunctionCallNode,
)
from error_reporter import ParseError

COMPARISON_OPS = {TT_EQ, TT_NEQ, TT_LT, TT_GT, TT_LE, TT_GE}
ADDITIVE_OPS = {TT_PLUS, TT_MINUS}
TERM_OPS = {TT_STAR, TT_SLASH}


class Parser:
    def __init__(self, tokens, sheet=None, cell=None):
        self.tokens = tokens
        self.pos = 0
        self.sheet = sheet   # for error messages only
        self.cell = cell     # for error messages only

    # ---- token stream helpers -----------------------------------
    def _current(self):
        return self.tokens[self.pos]

    def _check(self, *types):
        return self._current().type in types

    def _advance(self):
        tok = self.tokens[self.pos]
        if tok.type != TT_EOF:
            self.pos += 1
        return tok

    def _expect(self, type_):
        tok = self._current()
        if tok.type != type_:
            raise ParseError(
                f"unexpected token {tok.value!r} (type {tok.type}), expected {type_}",
                sheet=self.sheet, cell=self.cell, position=tok.position,
            )
        return self._advance()

    # ---- entry point -----------------------------------------------
    def parse(self):
        """formula := expression   (leading '=' already stripped by lexer)"""
        node = self._parse_comparison()
        self._expect(TT_EOF)
        return node

    # ---- comparison := concat (( = <> < > <= >= ) concat)* ----------
    def _parse_comparison(self):
        node = self._parse_concat()
        while self._check(*COMPARISON_OPS):
            op_tok = self._advance()
            right = self._parse_concat()
            node = BinaryOpNode(op_tok.value, node, right)
        return node

    # ---- concat := additive ('&' additive)* --------------------------
    def _parse_concat(self):
        node = self._parse_additive()
        while self._check(TT_AMP):
            op_tok = self._advance()
            right = self._parse_additive()
            node = BinaryOpNode(op_tok.value, node, right)
        return node

    # ---- additive := term (('+'|'-') term)* --------------------------
    def _parse_additive(self):
        node = self._parse_term()
        while self._check(*ADDITIVE_OPS):
            op_tok = self._advance()
            right = self._parse_term()
            node = BinaryOpNode(op_tok.value, node, right)
        return node

    # ---- term := unary (('*'|'/') unary)* -----------------------------
    def _parse_term(self):
        node = self._parse_unary()
        while self._check(*TERM_OPS):
            op_tok = self._advance()
            right = self._parse_unary()
            node = BinaryOpNode(op_tok.value, node, right)
        return node

    # ---- unary := '-' unary | power ------------------------------------
    def _parse_unary(self):
        if self._check(TT_MINUS):
            op_tok = self._advance()
            operand = self._parse_unary()
            return UnaryOpNode(op_tok.value, operand)
        return self._parse_power()

    # ---- power := primary ('^' unary)?   (right-associative) -----------
    def _parse_power(self):
        base = self._parse_primary()
        if self._check(TT_CARET):
            op_tok = self._advance()
            exponent = self._parse_unary()  # right-recursive -> right-assoc
            return BinaryOpNode(op_tok.value, base, exponent)
        return base

    # ---- primary := NUMBER | STRING | function_call | reference | '(' expr ')'
    def _parse_primary(self):
        tok = self._current()

        if tok.type == TT_NUMBER:
            self._advance()
            return NumberNode(tok.value)

        if tok.type == TT_STRING:
            self._advance()
            return StringNode(tok.value)

        if tok.type == TT_LPAREN:
            self._advance()
            node = self._parse_comparison()
            self._expect(TT_RPAREN)
            return node

        if tok.type == TT_CELL_REF:
            return self._parse_reference(sheet=None)

        if tok.type == TT_IDENTIFIER:
            # TRUE / FALSE are boolean literals, not named ranges --
            # checked before the general named-range fallback below.
            if tok.value in ("TRUE", "FALSE"):
                next_tok = self.tokens[self.pos + 1]
                if next_tok.type not in (TT_BANG, TT_LPAREN):
                    self._advance()
                    return BooleanNode(tok.value == "TRUE")

            # Lookahead decides: IDENTIFIER '!' -> sheet-qualified reference
            #                     IDENTIFIER '(' -> function call
            #                     IDENTIFIER      -> named range
            next_tok = self.tokens[self.pos + 1]
            if next_tok.type == TT_BANG:
                sheet_name = self._advance().value  # consume IDENTIFIER
                self._advance()                      # consume '!'
                return self._parse_reference(sheet=sheet_name)
            if next_tok.type == TT_LPAREN:
                return self._parse_function_call()
            self._advance()
            return NamedRangeNode(tok.value)

        raise ParseError(
            f"unexpected token {tok.value!r} (type {tok.type})",
            sheet=self.sheet, cell=self.cell, position=tok.position,
        )

    def _parse_reference(self, sheet):
        """cell_or_range := CELL_REF (':' CELL_REF)?"""
        start_tok = self._expect(TT_CELL_REF)
        if self._check(TT_COLON):
            self._advance()
            end_tok = self._expect(TT_CELL_REF)
            return RangeNode(start_tok.value, end_tok.value, sheet=sheet)
        return CellRefNode(start_tok.value, sheet=sheet)

    def _parse_function_call(self):
        """function_call := IDENTIFIER '(' arg_list? ')'"""
        name_tok = self._expect(TT_IDENTIFIER)
        self._expect(TT_LPAREN)
        args = []
        if not self._check(TT_RPAREN):
            args.append(self._parse_comparison())
            while self._check(TT_COMMA):
                self._advance()
                args.append(self._parse_comparison())
        self._expect(TT_RPAREN)
        return FunctionCallNode(name_tok.value, args)


def parse_formula(text, sheet=None, cell=None):
    """Convenience wrapper: lex + parse a raw formula string in one call."""
    from lexer import Lexer
    tokens = Lexer(text, sheet=sheet, cell=cell).tokenize()
    return Parser(tokens, sheet=sheet, cell=cell).parse()
