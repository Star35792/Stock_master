"""a-stock-data Skill 代码加载器（把 SKILL.md 内嵌 Python 变成可 import 的模块）。

背景
----
`a-stock-data-main/SKILL.md` 是「结构化 Markdown + 内嵌 Python」：87 个端点的
可运行代码直接写在 markdown 代码块里。它不是 pip 包，无法直接 import。

工程问题
--------
不少代码块在**模块级**就直接联网取数并打印演示结果：

    quote = tencent_quote(["600519", "000001"])   # 模块级就发请求
    print(quote)

直接 exec 整个块会导致：import 即发几十个外网请求（可能触发东财风控）、
stdout 被噪音灌满、加载耗时数十秒。

解决方案
--------
用 AST 白名单净化，只保留「可安全执行的定义」：

- ``Import`` / ``FunctionDef`` / ``AsyncFunctionDef`` / ``ClassDef`` —— 保留
- ``Assign`` / ``AnnAssign`` —— 仅当右侧是字面量或**安全构造调用**时保留
  （如 ``re.compile(...)``、``requests.Session()``）；右侧是业务取数调用
  （如 ``tencent_quote([...])``）时丢弃
- 其余（``Expr`` 打印与 demo 调用、``If``/``For``/``While``/``With``/``Try``
  控制流）—— 全部丢弃

``mootdx``（SKILL.md §1.7，2026-09 起失效留档）改软依赖，避免整块加载失败。

净化后得到一个纯函数库：import 零网络请求，加载耗时约 1 秒。
"""

from __future__ import annotations

import ast
import pathlib
import sys
import types

#: 本仓库内 a-stock-data Skill 的位置
DEFAULT_SKILL_PATH = pathlib.Path(__file__).resolve().parents[2] / "a-stock-data-main" / "SKILL.md"

#: 允许在模块级求值的「安全构造」调用：正则编译、Session 创建、容器构造等。
#: 这些不发起业务网络请求（``Session()`` 只建连接池，不发包），但属于模块状态，
#: 必须保留，否则依赖它们的函数会 NameError。
SAFE_CALLS = frozenset(
    {
        "re.compile",
        "re.match",
        "re.search",
        "re.findall",
        "re.split",
        "requests.Session",
        "Session",
        "dict",
        "list",
        "tuple",
        "set",
        "frozenset",
        "OrderedDict",
        "defaultdict",
        "Counter",
        "deque",
        "datetime",
        "date",
        "Decimal",
        "Fraction",
        "Path",
        "os.path.join",
        "os.path.dirname",
        "os.path.abspath",
    }
)

_DEF_TYPES = (
    ast.Import,
    ast.ImportFrom,
    ast.FunctionDef,
    ast.AsyncFunctionDef,
    ast.ClassDef,
)


def extract_blocks(text: str) -> list[tuple[int, str]]:
    """从 markdown 中抽出 ```python 代码块，返回 ``[(起始行号, 代码)]``。"""
    lines = text.split("\n")
    blocks: list[tuple[int, str]] = []
    i = 0
    while i < len(lines):
        if lines[i].strip() == "```python":
            j = i + 1
            body: list[str] = []
            while j < len(lines) and lines[j].strip() != "```":
                body.append(lines[j])
                j += 1
            blocks.append((i + 1, "\n".join(body)))
            i = j
        i += 1
    return blocks


def _attr_path(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _attr_path(node.value)
        return f"{base}.{node.attr}" if base else node.attr
    return ""


def _call_name(node: ast.Call) -> str:
    func = node.func
    if isinstance(func, ast.Attribute):
        base = _attr_path(func.value)
        return f"{base}.{func.attr}" if base else func.attr
    if isinstance(func, ast.Name):
        return func.id
    return ""


def _is_literal(node: ast.AST | None) -> bool:
    if node is None:
        return True
    if isinstance(node, ast.Constant):
        return True
    if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
        return all(_is_literal(e) for e in node.elts)
    if isinstance(node, ast.Dict):
        return all(
            _is_literal(k) and _is_literal(v)
            for k, v in zip(node.keys, node.values, strict=False)
        )
    if isinstance(node, ast.UnaryOp):
        return _is_literal(node.operand)
    return False


def _is_safe_value(node: ast.AST | None) -> bool:
    """判断赋值右侧能否在模块级安全求值（不发起业务网络请求）。"""
    if _is_literal(node):
        return True
    if isinstance(node, ast.Call):
        if _call_name(node) not in SAFE_CALLS:
            return False
        for arg in list(node.args) + [kw.value for kw in node.keywords]:
            if isinstance(arg, ast.Call):
                return False
            if not _is_literal(arg) and not isinstance(
                arg, (ast.Name, ast.Attribute, ast.Subscript)
            ):
                return False
        return True
    if isinstance(node, (ast.Name, ast.Attribute, ast.Subscript)):
        return True
    if isinstance(node, ast.BinOp):
        return _is_safe_value(node.left) and _is_safe_value(node.right)
    if isinstance(node, ast.JoinedStr):
        return all(
            _is_safe_value(v.value) if isinstance(v, ast.FormattedValue) else True
            for v in node.values
        )
    if isinstance(node, ast.Compare):
        return all(_is_safe_value(c) for c in [node.left, *node.comparators])
    if isinstance(node, ast.BoolOp):
        return all(_is_safe_value(v) for v in node.values)
    if isinstance(node, ast.IfExp):
        return (
            _is_safe_value(node.test)
            and _is_safe_value(node.body)
            and _is_safe_value(node.orelse)
        )
    return False


def sanitize(code: str) -> tuple[str, int]:
    """剥离模块级副作用。返回 ``(净化后代码, 丢弃语句数)``。"""
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return code, 0
    keep: list[ast.stmt] = []
    dropped = 0
    for node in tree.body:
        if isinstance(node, _DEF_TYPES):
            keep.append(node)
            continue
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            if _is_safe_value(node.value):
                keep.append(node)
            else:
                dropped += 1
            continue
        dropped += 1
    if not dropped:
        return code, 0
    new_tree = ast.Module(body=keep, type_ignores=[])
    ast.fix_missing_locations(new_tree)
    return ast.unparse(new_tree), dropped


def patch_optional_imports(code: str) -> str:
    """把 mootdx 依赖改为软依赖（该端点 2026-09 起失效留档）。"""
    return code.replace(
        "from mootdx.quotes import Quotes",
        "try:\n    from mootdx.quotes import Quotes\nexcept Exception:\n    Quotes = None",
    ).replace(
        "from mootdx.client import TdxHq_API",
        "try:\n    from mootdx.client import TdxHq_API\nexcept Exception:\n    TdxHq_API = None",
    )


def load(
    path: pathlib.Path | str = DEFAULT_SKILL_PATH,
    name: str = "a_stock_data",
) -> tuple[types.ModuleType, list[tuple[int, str]]]:
    """加载 Skill 代码块为模块。返回 ``(模块, 被跳过的块)``。"""
    skill_path = pathlib.Path(path)
    if not skill_path.exists():
        raise FileNotFoundError(f"未找到 a-stock-data SKILL.md: {skill_path}")

    text = skill_path.read_text(encoding="utf-8")
    mod = types.ModuleType(name)
    mod.__dict__["__builtins__"] = __builtins__
    mod.SKILL_PATH = str(skill_path)
    sys.modules[name] = mod

    skipped: list[tuple[int, str]] = []
    for lineno, code in extract_blocks(text):
        code, _dropped = sanitize(code)
        code = patch_optional_imports(code)
        try:
            exec(compile(code, f"<a-stock-data:{lineno}>", "exec"), mod.__dict__)
        except Exception as exc:  # 单块失败不影响其余块
            skipped.append((lineno, f"{type(exc).__name__}: {str(exc)[:150]}"))
    return mod, skipped


if __name__ == "__main__":
    module, failures = load()
    print(f"loaded from {module.SKILL_PATH}")
    print(f"skipped blocks: {len(failures)}")
    for line_no, message in failures:
        print(f"  line {line_no}: {message}")
    callables = sorted(
        n for n in dir(module) if not n.startswith("_") and callable(getattr(module, n))
    )
    print(f"public callables: {len(callables)}")
