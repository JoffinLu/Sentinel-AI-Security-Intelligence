"""运行时路径解析（开发环境 / PyInstaller 打包环境 双兼容）
=================================================================

开发环境：
    项目根 = 本文件的上一级（项目源码/ 的父级）
    → .env、数据/模型缓存、前端界面/dist 都在项目根下

PyInstaller onedir 打包环境（sys.frozen）：
    - **可写目录**（app_root）：exe 所在目录 —— .env、数据快照等用户可见、可编辑
    - **只读资源**（resource）：优先 exe 目录（允许用户外挂替换），
      其次打包内置目录（sys._MEIPASS，onedir 下即 _internal/）

使用约定：
    from ..运行时 import app_root, resource

    app_root() / ".env"          # 用户配置（可写）
    resource("数据", "模型缓存")   # 模型文件（只读，允许外挂）
"""

from __future__ import annotations

import sys
from pathlib import Path


def is_frozen() -> bool:
    """是否运行在 PyInstaller 打包环境"""
    return bool(getattr(sys, "frozen", False))


def app_root() -> Path:
    """应用根目录（可写）。

    - 打包环境：exe 所在目录（用户看得见，.env 放这里）
    - 开发环境：项目根目录（本文件的上一级）
    """
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def _bundle_dir() -> Path:
    """打包内置资源目录（PyInstaller 解压/内置目录）。

    onedir 模式下为 exe目录/_internal；onefile 模式为临时解压目录。
    开发环境下等于项目根。
    """
    if is_frozen():
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parent.parent


def resource(*parts: str) -> Path:
    """定位只读资源（模型缓存、前端 dist 等）。

    搜索顺序（打包环境）：
    ① exe 目录/<parts>        —— 允许用户外挂/替换（如换一个更大的模型）
    ② 内置目录/<parts>        —— 打包时随包携带的默认资源
    开发环境直接返回 项目根/<parts>。
    """
    if is_frozen():
        external = app_root().joinpath(*parts)
        if external.exists():
            return external
        return _bundle_dir().joinpath(*parts)
    return Path(__file__).resolve().parent.parent.joinpath(*parts)
