import ast
import re

GUARDS = {"_require_write_security", "_require_quick_fill_write"}
VERBS = {"do_POST", "do_PATCH", "do_PUT", "do_DELETE"}
TOKEN = re.compile(r"/[A-Za-z0-9_./-]+")
WORD = re.compile(r"[a-z][a-z/-]*")


# reads cannot need a tool
def mutating_tokens(source: str) -> set[str]:
    tree = ast.parse(source)
    handler = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "DashboardHandler")
    found: set[str] = set()
    for method in handler.body:
        if not isinstance(method, ast.FunctionDef):
            continue
        calls = {node.func.attr for node in ast.walk(method) if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)}
        routing = method.name.endswith("_route") and not method.name.startswith("_is_")
        if not (method.name in VERBS or routing or calls & GUARDS):
            continue
        for node in ast.walk(method):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value != "/" and TOKEN.fullmatch(node.value):
                found.add(node.value)
            # company actions live in a set
            if isinstance(node, ast.Set) and (routing or id(node) in action_sets(method)):
                found.update("/" + item.value for item in node.elts if isinstance(item, ast.Constant) and isinstance(item.value, str) and WORD.fullmatch(item.value))
    return found


def action_sets(method: ast.FunctionDef) -> set[int]:
    return {
        id(node.value) for node in ast.walk(method)
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Set) and any(isinstance(target, ast.Name) and target.id.endswith("actions") for target in node.targets)
    }
