"""
dependency_graph.py
====================
Compiler-design concept: INTERMEDIATE ANALYSIS / DATA-FLOW ORDERING.

Excel does not evaluate cells top-to-bottom; a cell's formula can
reference any other cell, including ones "later" in the sheet. Before
we can evaluate anything we must:

  1. Build a directed graph: edge (B -> A) means "A's formula
     references B, so A depends on B and B must be evaluated first."
  2. Detect circular references (e.g. A1 = B1+1, B1 = A1+1), which
     Excel itself flags as an error.
  3. Compute a topological evaluation order so that, by the time we
     evaluate any cell, every cell it depends on already has a value.

Algorithm used: iterative depth-first search for cycle detection
(3-colour / white-grey-black marking) plus DFS-based topological sort
(post-order reversal). Both are classic, textbook graph algorithms —
deliberately NOT using a third-party graph library, so the algorithm
is fully our own and explainable in a viva.
"""

from error_reporter import DependencyError


class DependencyGraph:
    def __init__(self):
        # adjacency: node -> set of nodes it depends on (its prerequisites)
        self._depends_on = {}

    def add_node(self, node):
        self._depends_on.setdefault(node, set())

    def add_dependency(self, node, depends_on_node):
        """`node`'s formula references `depends_on_node` --
        so depends_on_node must be evaluated before node."""
        self.add_node(node)
        self.add_node(depends_on_node)
        self._depends_on[node].add(depends_on_node)

    def nodes(self):
        return list(self._depends_on.keys())

    def dependencies_of(self, node):
        return self._depends_on.get(node, set())

    # ---- cycle detection: iterative DFS with 3-colour marking -----------
    # WHITE = unvisited, GREY = on current DFS path, BLACK = fully processed
    def find_cycle(self):
        WHITE, GREY, BLACK = 0, 1, 2
        colour = {n: WHITE for n in self._depends_on}
        parent = {}

        for start in self._depends_on:
            if colour[start] != WHITE:
                continue
            stack = [(start, iter(self._depends_on[start]))]
            colour[start] = GREY
            while stack:
                node, it = stack[-1]
                advanced = False
                for nxt in it:
                    if colour[nxt] == WHITE:
                        colour[nxt] = GREY
                        parent[nxt] = node
                        stack.append((nxt, iter(self._depends_on[nxt])))
                        advanced = True
                        break
                    elif colour[nxt] == GREY:
                        # Found a back-edge -> reconstruct the cycle path.
                        cycle = [nxt, node]
                        cur = node
                        while cur != nxt and cur in parent:
                            cur = parent[cur]
                            cycle.append(cur)
                        cycle.reverse()
                        return cycle
                    # if BLACK: already fully processed, safe to ignore
                if not advanced:
                    colour[node] = BLACK
                    stack.pop()
        return None  # no cycle found

    # ---- topological sort: DFS post-order, then reverse -------------------
    def topological_order(self):
        """Returns a list of nodes such that every node appears after
        all the nodes it depends on. Raises DependencyError if a cycle
        exists (call find_cycle() first for a friendlier message)."""
        cycle = self.find_cycle()
        if cycle:
            names = " -> ".join(f"{s}!{c}" if s else c for s, c in cycle)
            raise DependencyError(f"circular reference detected: {names}")

        visited = set()
        post_order = []

        def dfs(node):
            visited.add(node)
            for dep in self._depends_on.get(node, ()):
                if dep not in visited:
                    dfs(dep)
            post_order.append(node)

        for node in self._depends_on:
            if node not in visited:
                dfs(node)

        # post_order already lists dependencies before dependents,
        # because dfs() appends a node only after all its dependencies
        # have been appended.
        return post_order
