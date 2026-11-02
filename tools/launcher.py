"""Build self-contained launchers that select their adjacent TinyGeo runtime."""

import ast

LOCAL_RUNTIME = '''# Use this extracted bundle even in a persistent interpreter with older imports.
def _tinygeo_load_local():
    import importlib
    from pathlib import Path
    import sys

    folder = Path(__file__).resolve().parent
    package = folder / "tinygeo" / "__init__.py"
    module_file = folder / "tinygeo.py"
    expected = package if package.is_file() else module_file
    if not expected.is_file():
        raise ImportError(
            "TinyGeo runtime missing beside this script. Extract the whole ZIP, "
            "keeping tinygeo/__init__.py (or embedded tinygeo.py) beside it. "
            f"Script folder: {folder}"
        )
    sys.path.insert(0, str(folder))
    # A path change alone does not replace modules already cached by Pythonista.
    for name in tuple(sys.modules):
        if name == "tinygeo" or name.startswith("tinygeo."):
            del sys.modules[name]
    importlib.invalidate_caches()
    runtime = importlib.import_module("tinygeo")
    origin = getattr(runtime, "__file__", None)
    if origin is None or Path(origin).resolve() != expected.resolve():
        raise ImportError(f"Expected TinyGeo at {expected}, but imported {origin}")
    return runtime

_tinygeo_runtime = _tinygeo_load_local()

'''


def wrap_launcher(source):
    """Prefix a script without adding another runtime file or import dependency."""
    if isinstance(source, bytes):
        source = source.decode('utf-8')
    statements = ast.parse(source).body
    offset = 0
    for index, statement in enumerate(statements):
        if (index == 0 and isinstance(statement, ast.Expr)
                and isinstance(statement.value, ast.Constant) and isinstance(statement.value.value, str)):
            offset = statement.end_lineno
        elif isinstance(statement, ast.ImportFrom) and statement.module == '__future__':
            offset = statement.end_lineno
        else:
            break
    lines = source.splitlines(keepends=True)
    return (''.join(lines[:offset]) + '\n' + LOCAL_RUNTIME + ''.join(lines[offset:])).encode('utf-8')


def tracker_launcher(source, *, diagnostics=False):
    """Generate normal/debug tracker scripts from the same maintained source."""
    tree = ast.parse(source.decode('utf-8') if isinstance(source, bytes) else source)
    expected = {'DIAGNOSTICS', 'CAPTURE_NATIVE_FAULTS'}
    found = set()
    for node in tree.body:
        if (isinstance(node, ast.Assign) and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name) and node.targets[0].id in expected):
            found.add(node.targets[0].id)
            node.value = ast.Constant(bool(diagnostics))
    if found != expected:
        raise ValueError('tracker source is missing its diagnostic configuration flags')
    return wrap_launcher(ast.unparse(tree)+'\n')
