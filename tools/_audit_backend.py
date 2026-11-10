# -*- coding: utf-8 -*-
"""后端代码体检：把「屎山」量化成可对比的数字。

为什么要有这个
--------------
2026-11-09 主人要「翻看所有后端逻辑，写好注释，检查逻辑是否大量嵌套」。
光凭感觉说「这段有点乱」没法验收，也没法证明改完真的变好了。
所以把三件事变成机器可算的指标：

1. **注释覆盖** —— 函数有没有 docstring。docstring 只检查「有没有」，
   内容质量（是不是在复述代码）靠人看。
2. **嵌套深度** —— if/for/while/with/try 的最大嵌套层数。
   经验阈值：≤3 正常；4 要留意；≥5 基本读不动了（人脑一次只能稳住三四层）。
3. **圈复杂度** —— 判断 + 循环 + try + 布尔运算的近似值。
   参考 McCabe：≤10 简单，11~20 中等，≥21 建议拆。

用法（在项目根跑）：
    app\\.venv\\Scripts\\python.exe _qa\\_audit_backend.py
    app\\.venv\\Scripts\\python.exe _qa\\_audit_backend.py --md   # 输出 Markdown 报告

只看 `app/`，跳过 `.venv` / `__pycache__`。
"""
import argparse
import ast
import os
import sys

CTRL = (ast.If, ast.For, ast.While, ast.With, ast.Try, ast.AsyncFor, ast.AsyncWith)


def max_depth(node, depth=0):
    """控制流最大嵌套层数。函数/类边界不累加（那是另一个函数的事）。"""
    best = depth
    for child in ast.iter_child_nodes(node):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        nd = depth + 1 if isinstance(child, CTRL) else depth
        best = max(best, max_depth(child, nd))
    return best


def complexity(node):
    """圈复杂度近似值：每个判断/循环/try/三元 +1，布尔运算按操作数 -1。"""
    total = 1
    for item in ast.walk(node):
        if isinstance(item, (ast.If, ast.For, ast.While, ast.Try, ast.AsyncFor,
                             ast.AsyncWith, ast.IfExp)):
            total += 1
        elif isinstance(item, ast.BoolOp):
            total += len(item.values) - 1
    return total


def scan(root='app'):
    """返回 (文件统计, 函数统计)。两个列表都按「最该改」排好序。"""
    files, funcs = [], []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in ('.venv', '__pycache__')]
        for name in sorted(filenames):
            if not name.endswith('.py'):
                continue
            path = os.path.join(dirpath, name)
            try:
                source = open(path, encoding='utf-8').read()
                tree = ast.parse(source)
            except (OSError, SyntaxError) as exc:
                files.append(dict(path=path, error=str(exc), lines=0, funcs=0,
                                  nodoc=0, longest=0, longest_name=''))
                continue
            functions = [n for n in ast.walk(tree)
                         if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
            longest, longest_name = 0, ''
            for fn in functions:
                size = fn.end_lineno - fn.lineno + 1
                if size > longest:
                    longest, longest_name = size, fn.name
            files.append(dict(
                path=path, lines=source.count('\n') + 1, funcs=len(functions),
                nodoc=sum(1 for fn in functions if not ast.get_docstring(fn)),
                longest=longest, longest_name=longest_name, error=''))
            for fn in functions:
                funcs.append(dict(
                    path=path, name=fn.name, line=fn.lineno,
                    lines=fn.end_lineno - fn.lineno + 1,
                    depth=max_depth(fn), cplx=complexity(fn),
                    doc=bool(ast.get_docstring(fn))))
    funcs.sort(key=lambda r: (-(r['depth'] * 10 + r['lines'])))
    return files, funcs


def report(files, funcs):
    """把统计写成人能读的 Markdown。阈值都写出来，方便以后对比。"""
    total_lines = sum(f['lines'] for f in files)
    total_funcs = sum(f['funcs'] for f in files)
    total_nodoc = sum(f['nodoc'] for f in files)
    deep = [f for f in funcs if f['depth'] >= 5]
    hard = [f for f in funcs if f['cplx'] >= 21]
    long_ = [f for f in funcs if f['lines'] > 80]
    out = []
    out.append('# 后端代码体检报告\n')
    out.append(f'- 文件 {len(files)} 个 / {total_lines} 行')
    out.append(f'- 函数 {total_funcs} 个，其中 **{total_nodoc} 个没有 docstring'
               f'（{total_nodoc * 100 // max(1, total_funcs)}%）**')
    out.append(f'- 嵌套 ≥5 层：**{len(deep)}** 个（≥4 层 {sum(1 for f in funcs if f["depth"] >= 4)} 个）')
    out.append(f'- 圈复杂度 ≥21：**{len(hard)}** 个')
    out.append(f'- 超过 80 行：**{len(long_)}** 个\n')
    out.append('## 最该改的 25 个函数（按嵌套深度 × 10 + 行数排）\n')
    out.append('| 文件 | 函数 | 行 | 行数 | 深度 | 复杂度 | 有说明 |')
    out.append('|---|---|---|---|---|---|---|')
    for item in funcs[:25]:
        out.append('| {} | `{}` | {} | {} | {} | {} | {} |'.format(
            item['path'].replace('app' + os.sep, ''), item['name'], item['line'],
            item['lines'], item['depth'], item['cplx'], '是' if item['doc'] else '**否**'))
    out.append('\n## 按文件看\n')
    out.append('| 文件 | 行数 | 函数 | 无说明 | 最长函数 |')
    out.append('|---|---|---|---|---|')
    for item in sorted(files, key=lambda r: -r['lines']):
        out.append('| {} | {} | {} | {} | `{}` {} 行 |'.format(
            item['path'].replace('app' + os.sep, ''), item['lines'], item['funcs'],
            item['nodoc'], item['longest_name'], item['longest']))
    return '\n'.join(out)


def main():
    parser = argparse.ArgumentParser(description='后端代码体检')
    parser.add_argument('--md', action='store_true', help='输出 Markdown 报告')
    parser.add_argument('--root', default='app', help='要扫的目录，默认 app')
    args = parser.parse_args()
    files, funcs = scan(args.root)
    if args.md:
        print(report(files, funcs))
        return 0
    total_funcs = sum(f['funcs'] for f in files)
    total_nodoc = sum(f['nodoc'] for f in files)
    print(f'{len(files)} 文件 / {sum(f["lines"] for f in files)} 行 / '
          f'{total_funcs} 函数，无说明 {total_nodoc}')
    print(f'嵌套≥5: {sum(1 for f in funcs if f["depth"] >= 5)}  '
          f'复杂度≥21: {sum(1 for f in funcs if f["cplx"] >= 21)}  '
          f'超80行: {sum(1 for f in funcs if f["lines"] > 80)}')
    for item in funcs[:15]:
        print('  {:28s} {:22s} L{:<5d} {:4d}行 dep{} cplx{}'.format(
            item['path'].replace('app' + os.sep, ''), item['name'],
            item['line'], item['lines'], item['depth'], item['cplx']))
    return 0


if __name__ == '__main__':
    sys.exit(main())
