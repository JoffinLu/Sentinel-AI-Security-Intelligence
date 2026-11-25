# -*- mode: python ; coding: utf-8 -*-
"""SENTINEL 打包配置（PyInstaller spec）

用法（在项目根目录）：
    ./.venv/Scripts/pyinstaller.exe Sentinel.spec --noconfirm \
        --distpath 发布/_build --workpath build/pyinstaller

产物：发布/_build/Sentinel/Sentinel.exe（onedir）
发布包组装（compose / .env / snapshot）见 发布/ 组装说明，不在 spec 内。
"""

from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files

# 项目根（spec 文件所在目录）
ROOT = Path(SPECPATH)

a = Analysis(
    ['项目源码/启动器.py'],
    pathex=[str(ROOT)],
    binaries=[],
    # 只读资源随包携带：
    #   模型缓存（BGE-small-zh 91MB + ROEN 1.7MB，运行时经 resource() 定位）
    #   拓展资源（ROEN 演示图像，数据/拓展资源）
    #   前端 dist（静态托管，运行时经 resource() 定位）
    datas=[
        # torch_geometric：① .jinja 聚合模板（MessagePassing 首次调用时渲染）；
        # ② **.py 源码**——tg 在 import 链上对若干类执行 torch.jit.script，
        #    TorchScript 必须读取原始源码；若只留 PYZ 里的字节码，冻结环境
        #    会报 "OSError: Can't get source for <class ...>"（dev 环境不炸、
        #    只有打包后炸的隐蔽崩溃——2026-10-14 实测踩过）。
        *collect_data_files('torch_geometric', include_py_files=True),
        ('数据/模型缓存', '数据/模型缓存'),
        ('数据/拓展资源', '数据/拓展资源'),
        ('前端界面/dist', '前端界面/dist'),
        # 内嵌 PostgreSQL（零依赖分发）：只挑选运行必需的三个子目录，
        # pgAdmin/StackBuilder/doc/include 等无关组件不进包（省 ~700MB）。
        # 来源：官方 Windows 二进制包 + pgvector 0.8.6-pg16 预编译扩展。
        ('发布/_emb/pgsql/bin', 'pgsql/bin'),
        ('发布/_emb/pgsql/lib', 'pgsql/lib'),
        ('发布/_emb/pgsql/share', 'pgsql/share'),
    ],
    hiddenimports=[
        # uvicorn 的动态导入（loop / 协议实现按字符串加载，静态分析收不到）
        'uvicorn.logging',
        'uvicorn.loops',
        'uvicorn.loops.auto',
        'uvicorn.loops.asyncio',
        'uvicorn.protocols',
        'uvicorn.protocols.http',
        'uvicorn.protocols.http.auto',
        'uvicorn.protocols.http.h11_impl',
        'uvicorn.protocols.http.httptools_impl',
        'uvicorn.protocols.websockets',
        'uvicorn.protocols.websockets.auto',
        'uvicorn.protocols.websockets.websockets_impl',
        'uvicorn.protocols.websockets.wsproto_impl',
        'uvicorn.lifespan',
        'uvicorn.lifespan.on',
        # 数据库驱动（asyncpg 走 C 扩展，方言加载按字符串）
        'asyncpg',
        'asyncpg.protocol',
        'asyncpg.pgproto',
        'asyncpg.pgproto.pgproto',
        'psycopg',
        'pgvector',
        # APScheduler 后端按字符串加载
        'apscheduler.executors.asyncio',
        'apscheduler.jobstores.memory',
        'apscheduler.triggers.interval',
        # 拓展模块 ROEN（GCN 层在 torch_geometric.nn 下，静态分析未必收全）
        'torch_geometric',
        'torch_geometric.nn',
        'jinja2',  # torch_geometric 聚合模板渲染依赖（惰性导入）
        # 嵌入模型（transformers 的模型类按配置动态加载）
        'transformers',
        'transformers.models.bert',
        'transformers.models.bert.modeling_bert',
        'sentence_transformers',
        # pywebview 内嵌应用窗口（Windows 走 WebView2/EdgeChromium 后端）
        'webview',
        'webview.platforms.winforms',
        'webview.platforms.edgechromium',
        'clr_loader',
        'pythonnet',
        'bottle',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # 开发期工具不打进发布包（省体积）
    # ⚠️ 不能再排 PIL：拓展模块（ROEN）的混沌加密用 Pillow 读写图像
    excludes=['pytest', 'ruff', 'IPython', 'tkinter', 'matplotlib'],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='Sentinel',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,  # 不用 UPX：杀毒软件误报率高
    console=False,  # GUI 模式：双击不显示黑窗口（进度显示在启动画面上）
    icon='发布/logo.ico',
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='Sentinel',
)
