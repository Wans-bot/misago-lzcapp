#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""修正 Misago 主题页时间戳的英文残留（moment.js locale 对齐）。

背景：主题页帖子时间由前端 React 组件以 moment().fromNow() 渲染；内建初始化器
执行 moment.locale($("html").attr("lang"))。html lang 取自
conf/context_processors.py 的 get_language()[:2] —— 中文（zh-hans）环境得到
"zh"，而 bundle 内简体中文的 moment locale 名是 "zh-cn"（没有 "zh"），
查找失败后 moment 保持默认 "en"，表现为 "8 hours ago"、
"October 13, 2026, 6:41 AM" 等英文格式。

本脚本把取短码的逻辑换成与 moment locale 名对齐的映射；幂等，可重复执行。
"""

import os
import shutil

PATH = "/usr/local/lib/python3.12/site-packages/misago/conf/context_processors.py"

OLD = '"LANGUAGE_CODE_SHORT": get_language()[:2],'
NEW = (
    '"LANGUAGE_CODE_SHORT": {\n'
    '            "zh": "zh-cn",\n'
    '            "zh-hans": "zh-cn",\n'
    '            "zh-hant": "zh-tw",\n'
    '        }.get(get_language().lower(), get_language()[:2]),'
)

src = open(PATH, encoding="utf-8").read()

if NEW in src:
    print("[lzc-patch] context_processors.py 已打过补丁，跳过")
    raise SystemExit(0)

if OLD not in src:
    print("[lzc-patch] 未找到目标行（版本不符？），跳过")
    raise SystemExit(1)

src = src.replace(OLD, NEW, 1)
with open(PATH, "w", encoding="utf-8") as fh:
    fh.write(src)

# 清理字节码缓存，确保 uwsgi 启动时重新编译该模块
shutil.rmtree(os.path.join(os.path.dirname(PATH), "__pycache__"), ignore_errors=True)
print("[lzc-patch] context_processors.py 已修补：html lang 与 moment locale 对齐")
