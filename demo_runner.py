import sys
import traceback
from colorama import init, Fore, Style

# Initialize terminal colors
init(autoreset=True)

# ---------------------------------------------------------
# IMPORT YOUR MODULES HERE
# Adjust these imports to match your actual sheetcompile codebase
# ---------------------------------------------------------
try:
    from lexer import Lexer
    from parser import Parser
    from semantic_analyzer import SemanticAnalyzer  # Updated module name
    from ir_generator import IRGenerator
    from code_generator import CodeGenerator  # Updated module name
except ImportError as e:
    print(f"{Fore.RED}Import Error: {e}{Style.RESET_ALL}")
    print("Ensure demo_runner.py is in the root directory and module names match.")

    # Proceeding with dummy classes for demonstration purposes if imports fail
    class DummyPhase:
        def __init__(self, *args, **kwargs):
            pass

        def process(self, data):
            return f"Processed: {data}"

    Lexer = Parser = SemanticAnalyzer = IRGenerator = CodeGenerator = DummyPhase


def compile_formula(formula):
    print(f"\n{Fore.CYAN}{'='*50}")
    print(f"{Fore.YELLOW}Compiling Formula: {Style.BRIGHT}{formula}")
    print(f"{Fore.CYAN}{'='*50}")

    try:
        # Phase 1: Lexical Analysis
        print(f"{Fore.GREEN}[Phase 1] Lexer{Style.RESET_ALL}")
        lexer = Lexer(formula)
        tokens = lexer.tokenize() if hasattr(lexer, "tokenize") else lexer.process()
        print(f"Tokens: {tokens}\n")

        # Phase 2: Syntax Analysis (Parsing)
        print(f"{Fore.GREEN}[Phase 2] Parser{Style.RESET_ALL}")
        parser = Parser(tokens)
        ast = parser.parse() if hasattr(parser, "parse") else parser.process(tokens)
        print(f"AST: {ast}\n")

        # Phase 3: Semantic Analysis & Symbol Table
        print(f"{Fore.GREEN}[Phase 3] Semantic Analyzer{Style.RESET_ALL}")
        semantic = SemanticAnalyzer(ast)
        annotated_ast, symbol_table = (
            semantic.analyze()
            if hasattr(semantic, "analyze")
            else (ast, {"A1": "int", "B1": "int"})
        )
        print(f"Symbol Table State: {symbol_table}\n")

        # Phase 4: Intermediate Representation (IR / TAC)
        print(f"{Fore.GREEN}[Phase 4] IR Generator{Style.RESET_ALL}")
        ir_gen = IRGenerator(symbol_table)
        raw_tac = ir_gen.generate_cell(annotated_ast, "Sheet1", "A1")
        tac_output = "\n".join([instr.format_tac() for instr in raw_tac])
        print(f"Three-Address Code (TAC):\n{tac_output}\n")

        # Phase 5: Target Code Generation (Python)
        print(f"{Fore.GREEN}[Phase 5] Target Code Generator{Style.RESET_ALL}")

        # FIX: Convert custom Quadruple objects into standard Python tuples
        # so code_generator.py can safely unpack them.
        ir_tuples = [(q.op, q.arg1, q.arg2, q.result) for q in raw_tac]

        codegen = CodeGenerator(ir_tuples)
        final_code = codegen.generate()
        print(f"{Fore.MAGENTA}Final Python Code:\n{final_code}{Style.RESET_ALL}\n")

        print(
            f"{Fore.GREEN}SUCCESS: Compilation finished without errors.{Style.RESET_ALL}"
        )

    except Exception as e:
        error_type = type(e).__name__
        print(
            f"\n{Fore.RED}{Style.BRIGHT}COMPILATION HALTED: {error_type}{Style.RESET_ALL}"
        )
        print(f"{Fore.RED}System Error: {str(e)}{Style.RESET_ALL}")
        print(f"{Fore.LIGHTBLACK_EX}{traceback.format_exc()}{Style.RESET_ALL}")


def main():
    print(
        f"{Fore.BLUE}{Style.BRIGHT}Excel Formula Compiler Pipeline Demonstration{Style.RESET_ALL}"
    )

    # Updated test formulas compatible with current parser grammar
    test_formulas = [
        "=A1+B1",  # Simple arithmetic
        "=SUM(A1:A5)",  # Function call with range
        "=IF(C1>100, 1, 0)",  # Conditional logic
        "=VLOOKUP(D1, A1:B10, 2, FALSE)",  # Function call (local range)
        "=(A1*B1)-(C1/D1)^2",  # Precedence and nested operations
    ]

    for formula in test_formulas:
        compile_formula(formula)
        input(
            f"{Fore.LIGHTBLACK_EX}Press Enter to process the next formula...{Style.RESET_ALL}\n"
        )


if __name__ == "__main__":
    main()
