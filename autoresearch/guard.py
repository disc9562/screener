"""candidate.py 的靜態檢查：import 白名單，擋掉碰得到 harness、檔案、網路或呼叫端 frame 的寫法。

這是減速帶不是沙盒；評分端另外在 import candidate 前讀好手續費與期間、當參數傳入，評完再比對引擎函式。
"""
import ast
from pathlib import Path

ALLOWED_IMPORTS = frozenset({"numpy", "pandas", "math", "strategy.shotgun_v3", "strategy.shadow_lists",
                             "autoresearch.indicators", "autoresearch.bars", "config"})
# 任何識別字（變數、函式、參數、屬性）都不能是這些
FORBIDDEN_NAMES = frozenset({"open", "exec", "eval", "compile", "__import__", "globals", "locals", "vars",
                             "setattr", "delattr", "getattr", "input", "breakpoint"})
# 屬性與 from-import 的名字：前四個是拿回可寫陣列的後門；其餘通往 os/sys/import/frame 或讀檔
FORBIDDEN_ATTRS = frozenset({"setflags", "flags", "base", "ctypes",
                             "os", "sys", "io", "builtins", "importlib", "inspect", "gc", "subprocess", "socket",
                             "f_back", "f_globals", "f_locals", "gi_frame", "cr_frame", "ag_frame", "tb_frame",
                             "npyio", "load", "loadtxt", "genfromtxt", "fromfile", "memmap", "open_memmap",
                             "DataSource"})
# 就地修改內容的方法。對 import 進來的物件呼叫，等於把狀態存進不會隨 candidate 重新載入的模組
MUTATING_METHODS = frozenset({"append", "extend", "insert", "remove", "pop", "popitem", "clear", "add", "discard",
                              "update", "setdefault", "sort", "reverse", "fill", "put", "itemset", "resize",
                              "partition"})


def _imported(tree) -> dict[str, bool]:
    """import 綁定的名字 -> 是不是整個模組。from-import 的名字可能就是容器，一律當物件。"""
    out = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                out[a.asname or a.name.split(".")[0]] = True
        elif isinstance(node, ast.ImportFrom):
            for a in node.names:
                out[a.asname or a.name] = False
    return out


def _reaches_imported(node, imported) -> bool:
    """node 是不是 import 進來的模組裡的物件。只走屬性和下標，經過函式呼叫就是新物件。"""
    steps = 0
    while isinstance(node, (ast.Attribute, ast.Subscript)):
        node, steps = node.value, steps + 1
    return isinstance(node, ast.Name) and node.id in imported and (steps > 0 or not imported[node.id])


def _import_problem(node) -> str | None:
    if isinstance(node, ast.ImportFrom):
        if node.level:
            return "relative import"
        for a in node.names:
            full = f"{node.module}.{a.name}"
            if a.name == "*" or (node.module not in ALLOWED_IMPORTS and full not in ALLOWED_IMPORTS):
                return f"import {full}"
        return None
    for a in node.names:
        if a.name not in ALLOWED_IMPORTS:
            return f"import {a.name}"
        if "." in a.name and not a.asname:  # import a.b 會綁定整個 a 套件（autoresearch.engine 等都在上面）
            return f"import {a.name} without as"
    return None


def _idents(node):
    """(識別字, 是否屬性／import 進來的名字)"""
    if isinstance(node, ast.Name):
        yield node.id, False
    elif isinstance(node, ast.Attribute):
        yield node.attr, True
    elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        yield node.name, False
    elif isinstance(node, ast.arg):
        yield node.arg, False
    elif isinstance(node, ast.keyword) and node.arg:
        yield node.arg, False
    elif isinstance(node, ast.alias):
        yield from ((part, True) for part in node.name.split("."))
        if node.asname:
            yield node.asname, False


def check_candidate_source(path) -> str | None:
    """回傳最前面的違規 `forbidden: <what> at line N`；沒有就 None。語法錯誤直接丟 SyntaxError。"""
    tree = ast.parse(Path(path).read_text(encoding="utf-8"))
    imported = _imported(tree)
    bad = []
    for node in ast.walk(tree):
        line = getattr(node, "lineno", 0)
        if isinstance(node, (ast.Import, ast.ImportFrom)) and (what := _import_problem(node)):
            bad.append((line, what))
        if isinstance(node, ast.Attribute) and isinstance(node.ctx, (ast.Store, ast.Del)):
            # 一律不准對屬性賦值／刪除：擋 E.X = 0，也擋 m = np; m.max = ... 這種別名繞法
            bad.append((line, f"attribute assignment {ast.unparse(node)}"))
        # 改 import 進來的容器：config.L.append(1)、TF_MINUTES["3h"] = 180、L += [1]
        if isinstance(node, ast.Subscript) and isinstance(node.ctx, (ast.Store, ast.Del)) \
                and _reaches_imported(node.value, imported):
            bad.append((line, f"item assignment {ast.unparse(node)}"))
        if isinstance(node, ast.AugAssign) and _reaches_imported(node.target, imported):
            bad.append((line, f"augmented assignment {ast.unparse(node.target)}"))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                and node.func.attr in MUTATING_METHODS and _reaches_imported(node.func.value, imported):
            bad.append((line, f"mutating call {ast.unparse(node.func)}"))
        for name, is_attr in _idents(node):
            if name in FORBIDDEN_NAMES or (name.startswith("__") and name.endswith("__")):
                bad.append((line, f"name {name}"))
            elif is_attr and (name in FORBIDDEN_ATTRS or name.startswith("read_")):
                bad.append((line, f"attribute {name}"))
    if not bad:
        return None
    line, what = min(bad)
    return f"forbidden: {what} at line {line}"
