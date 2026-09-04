"""Static contract: production SDK requests must enter shared LLM admission."""

from __future__ import annotations

import ast
import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
EXEMPT = ROOT / "data_sources" / "llm_failover.py"
PRODUCTION_DIRS = (
    "agents",
    "api",
    "cache",
    "data_prep",
    "data_sources",
    "lib",
    "outreach",
    "publication",
    "research",
    "scripts",
)


def _production_python_files() -> list[pathlib.Path]:
    """Return repository-owned runtime sources, never environment/vendor files."""
    files = list(ROOT.glob("*.py"))
    for directory in PRODUCTION_DIRS:
        source_root = ROOT / directory
        if source_root.is_dir():
            files.extend(source_root.rglob("*.py"))
    return sorted(set(files))


def _is_sdk_create(call: ast.Call) -> bool:
    func = call.func
    return (
        isinstance(func, ast.Attribute)
        and func.attr == "create"
        and isinstance(func.value, ast.Attribute)
        and func.value.attr in {"messages", "completions", "responses"}
    )


class LlmAdmissionGuardTests(unittest.TestCase):
    def test_every_production_sdk_create_is_wrapped(self) -> None:
        violations: list[str] = []
        for path in _production_python_files():
            if path == EXEMPT:
                continue  # shared implementation is itself the admission point
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            parents = {
                child: parent
                for parent in ast.walk(tree)
                for child in ast.iter_child_nodes(parent)
            }
            for call in (node for node in ast.walk(tree)
                         if isinstance(node, ast.Call) and _is_sdk_create(node)):
                node: ast.AST = call
                wrapped = False
                while node in parents:
                    node = parents[node]
                    if (isinstance(node, ast.Call)
                            and isinstance(node.func, ast.Name)
                            and node.func.id == "call_with_backoff"):
                        wrapped = True
                        break
                if not wrapped:
                    violations.append(f"{path.relative_to(ROOT)}:{call.lineno}")
        self.assertEqual(
            violations, [],
            "direct SDK creates bypass shared LLM admission: " + ", ".join(violations))


if __name__ == "__main__":
    unittest.main()