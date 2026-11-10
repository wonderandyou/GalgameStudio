# -*- coding: utf-8 -*-
"""清理 Kimi 留在生产页面里的自动验收钩子（?assetstest / ?assetdeletetest / ?asseterrortest）。

为什么删：这些参数一进 URL 就会**真上传、真删除**项目里的登记数据。
正常路径不会触发，但生产页面不该带会写数据的自动钩子 —— 谁误点一个带参数的
链接，项目里就多出两条 "kimi-真实上传"。

保留 `window.GalAssetsTest`（只导出函数，不自动执行，手动调才生效）。
"""
import pathlib
import sys

TARGET = pathlib.Path(r'D:\软件\GalgameStudio\ui\assets.js')
START_MARK = '// 真实接口验收钩子'
END_MARK = '},600);'


def main():
    lines = TARGET.read_text(encoding='utf-8').splitlines(keepends=True)
    start = next((i for i, ln in enumerate(lines) if START_MARK in ln), None)
    if start is None:
        print('[跳过] 没找到验收钩子（可能已经清理过）')
        return 0
    end = None
    for i in range(start, len(lines)):
        if END_MARK in lines[i] and lines[i].strip() == END_MARK:
            end = i
            break
    if end is None:
        print(f'[FAIL] 找到起点（第 {start + 1} 行）但没找到终点 {END_MARK!r}，不动文件')
        return 2

    removed = end - start + 1
    backup = TARGET.with_suffix('.js.bak_hook')
    backup.write_text(TARGET.read_text(encoding='utf-8'), encoding='utf-8')

    kept = lines[:start] + lines[end + 1:]
    TARGET.write_text(''.join(kept), encoding='utf-8')
    print(f'[OK] 删掉第 {start + 1}~{end + 1} 行，共 {removed} 行')
    print(f'[OK] 备份 {backup.name}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
