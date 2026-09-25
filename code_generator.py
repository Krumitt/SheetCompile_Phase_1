"""
Phase 3: Code Generator
Translates Intermediate Representation (TAC/Quadruples) into executable Python/pandas logic.
"""

import pandas as pd
import numpy as np


class CodeGenerator:
    def __init__(self, ir_quadruples, input_dataframe_name="df"):
        """
        Initializes the code generator with a list of TAC quadruples.
        Format expected: (OPERATOR, ARG1, ARG2, RESULT)
        """
        self.ir = ir_quadruples
        self.input_df = input_dataframe_name

        # Preamble for the generated Python script
        self.code_lines = [
            "import pandas as pd",
            "import numpy as np",
            "",
            f"def execute_compiled_sheet({self.input_df}):",
            f"    # Ensure input is a DataFrame",
            f"    if not isinstance({self.input_df}, pd.DataFrame):",
            f"        raise TypeError('Input must be a pandas DataFrame')",
            "",
        ]

        self.expression_cache = {}

    def _emit(self, line):
        """Appends a line of code with proper function-level indentation."""
        self.code_lines.append(f"    {line}")

    def generate(self):
        """Iterates through IR quadruples and emits equivalent pandas operations."""
        pending_params = []

        for quad in self.ir:
            op, arg1, arg2, result = quad

            expr_sig = f"{op}:{arg1}:{arg2}"

            if expr_sig in self.expression_cache and op not in (
                "ASSIGN",
                "PARAM",
                "CALL",
            ):
                cached_result = self.expression_cache[expr_sig]
                self._emit(f"# OPTIMIZATION: Reusing cached computation for {expr_sig}")
                self._emit(f"{result} = {cached_result}")
                continue

            # Map IR Operations from ir_generator.py to Pandas/Python logic
            if op == "+":
                self._emit(f"{result} = {arg1} + {arg2}")
                self.expression_cache[expr_sig] = result

            elif op == "-":
                self._emit(f"{result} = {arg1} - {arg2}")
                self.expression_cache[expr_sig] = result

            elif op == "*":
                self._emit(f"{result} = {arg1} * {arg2}")
                self.expression_cache[expr_sig] = result

            elif op == "/":
                self._emit(f"{result} = np.where({arg2} == 0, np.nan, {arg1} / {arg2})")
                self.expression_cache[expr_sig] = result

            elif op == "^":
                self._emit(f"{result} = ({arg1}) ** ({arg2})")
                self.expression_cache[expr_sig] = result

            elif op == "UMINUS":
                self._emit(f"{result} = -{arg1}")
                self.expression_cache[expr_sig] = result

            elif op == "PARAM":
                pending_params.append(str(arg1))

            elif op == "CALL":
                func_name = str(arg1).upper()
                arg_count = int(arg2) if arg2 is not None else len(pending_params)
                call_args = pending_params[-arg_count:] if arg_count > 0 else []
                pending_params = (
                    pending_params[:-arg_count] if arg_count > 0 else pending_params
                )

                args_str = ", ".join(call_args)

                if func_name == "SUM":
                    self._emit(
                        f"{result} = sum([{args_str}]) if isinstance({args_str}, (list, pd.Series)) else {args_str}"
                    )
                else:
                    self._emit(f"{result} = {func_name}({args_str})")
                self.expression_cache[expr_sig] = result

            elif op == "ASSIGN":
                cell_name = str(result)
                if "!" in cell_name:
                    cell_name = cell_name.split("!")[1]

                # Check if it's a cell reference like A1 or column store
                col_letters = "".join([c for c in cell_name if c.isalpha()])
                row_digits = "".join([c for c in cell_name if c.isdigit()])

                if row_digits and col_letters:
                    row_idx = int(row_digits) - 1
                    self._emit(
                        f"{self.input_df}.at[{row_idx}, '{col_letters}'] = {arg1}"
                    )
                else:
                    self._emit(f"{self.input_df}['{cell_name}'] = {arg1}")

            # Legacy support for mock testing formats
            elif op == "ADD":
                self._emit(f"{result} = {arg1} + {arg2}")
                self.expression_cache[expr_sig] = result
            elif op == "SUB":
                self._emit(f"{result} = {arg1} - {arg2}")
                self.expression_cache[expr_sig] = result
            elif op == "MUL":
                self._emit(f"{result} = {arg1} * {arg2}")
                self.expression_cache[expr_sig] = result
            elif op == "DIV":
                self._emit(f"{result} = np.where({arg2} == 0, np.nan, {arg1} / {arg2})")
                self.expression_cache[expr_sig] = result
            elif op == "STORE_COL":
                self._emit(f"{self.input_df}['{arg1}'] = {result}")
            elif op == "STORE_CELL":
                self._emit(f"{self.input_df}.at[{arg2}, '{arg1}'] = {result}")

            else:
                self._emit(f"# WARNING: Unrecognized operation {op}")

        # Return the modified DataFrame at the end of the function
        self._emit(f"return {self.input_df}")

        return "\n".join(self.code_lines)

    def write_to_file(self, filename="compiled_output.py"):
        """Writes the generated python code to a file."""
        code = self.generate()
        with open(filename, "w") as f:
            f.write(code)
        print(f"[*] Compilation successful. Code generated and saved to '{filename}'.")
