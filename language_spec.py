"""
language_spec.py
=================
Compiler-design concept: LANGUAGE SPECIFICATION.

This module is the single source of truth for the subset of Excel formula
syntax that SheetCompile Phase 1 understands. Every other module (lexer,
parser, evaluator) imports its constants from here instead of hard-coding
strings, so the supported language is defined in exactly one place.

SUPPORTED SUBSET (matches the project proposal, Section 5 - Scope)
--------------------------------------------------------------------
Operators:      +  -  *  /  ^  =  <>  <  >  <=  >=
References:     A1, $A$1, $A1, A$1, A1:B10, Sheet1!A1
Functions:      SUM, AVERAGE, COUNT, COUNTA, COUNTIF, SUMIF, SUMIFS,
                MIN, MAX, IF, IFS, AND, OR, NOT, VLOOKUP, HLOOKUP,
                INDEX, MATCH, DATEDIF, CONCAT, CONCATENATE, TEXT,
                LEFT, RIGHT, MID, TRIM, IFERROR, ISERROR
Literals:       numbers (123, 4.5), strings ("text")

GRAMMAR (EBNF)
--------------
    formula      := '=' expression

    expression   := comparison

    comparison   := concat ( ( '=' | '<>' | '<' | '>' | '<=' | '>=' ) concat )*

    concat       := additive ( '&' additive )*

    additive     := term ( ( '+' | '-' ) term )*

    term         := unary ( ( '*' | '/' ) unary )*

    unary        := '-' unary
                   | power

    power        := primary ( '^' unary )?      (* right-associative *)

    primary      := NUMBER
                   | STRING
                   | function_call
                   | reference
                   | '(' expression ')'

    function_call:= IDENTIFIER '(' arg_list? ')'

    arg_list     := expression ( ',' expression )*

    reference    := ( IDENTIFIER '!' )? cell_or_range

    cell_or_range:= CELL_REF ( ':' CELL_REF )?
                   | IDENTIFIER                  (* named range *)

PRECEDENCE (lowest to highest binding power)
---------------------------------------------
    1. comparison operators   (= <> < > <= >=)
    2. concatenation           (&)
    3. + -                     (additive, left-associative)
    4. * /                     (multiplicative, left-associative)
    5. unary minus
    6. ^                       (power, right-associative)

This mirrors real Excel precedence rules (unary minus binds tighter than
+/- but looser than ^; ^ is right-associative, e.g. 2^3^2 = 2^(3^2)).
"""

# --- Token type names -------------------------------------------------
TT_NUMBER      = "NUMBER"
TT_STRING      = "STRING"
TT_CELL_REF    = "CELL_REF"
TT_IDENTIFIER  = "IDENTIFIER"
TT_PLUS        = "PLUS"
TT_MINUS       = "MINUS"
TT_STAR        = "STAR"
TT_SLASH       = "SLASH"
TT_CARET       = "CARET"
TT_AMP         = "AMP"          # & (text concatenation)
TT_EQ          = "EQ"           # =
TT_NEQ         = "NEQ"          # <>
TT_LE          = "LE"           # <=
TT_GE          = "GE"           # >=
TT_LT          = "LT"           # <
TT_GT          = "GT"           # >
TT_LPAREN      = "LPAREN"
TT_RPAREN      = "RPAREN"
TT_COMMA       = "COMMA"
TT_COLON       = "COLON"
TT_BANG        = "BANG"         # ! (sheet reference separator)
TT_EOF         = "EOF"

# --- Function names this subset supports -------------------------------
SUPPORTED_FUNCTIONS = {
    "SUM", "AVERAGE", "COUNT", "COUNTA", "COUNTIF", "SUMIF", "SUMIFS",
    "MIN", "MAX",
    "IF", "IFS", "AND", "OR", "NOT",
    "VLOOKUP", "HLOOKUP", "INDEX", "MATCH",
    "DATEDIF",
    "CONCAT", "CONCATENATE", "TEXT", "LEFT", "RIGHT", "MID", "TRIM",
    "IFERROR", "ISERROR",
}

# --- Single/double character operator table (order matters: check 2-char first)
TWO_CHAR_OPERATORS = {
    "<>": TT_NEQ,
    "<=": TT_LE,
    ">=": TT_GE,
}

ONE_CHAR_OPERATORS = {
    "+": TT_PLUS,
    "-": TT_MINUS,
    "*": TT_STAR,
    "/": TT_SLASH,
    "^": TT_CARET,
    "&": TT_AMP,
    "=": TT_EQ,
    "<": TT_LT,
    ">": TT_GT,
    "(": TT_LPAREN,
    ")": TT_RPAREN,
    ",": TT_COMMA,
    ":": TT_COLON,
    "!": TT_BANG,
}
