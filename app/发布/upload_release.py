"""创建 GitHub Release 并上传两个发布包（临时脚本，位于 gitignore 的 发布/ 下）"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time

import requests

REPO = "JoffinLu/ICT-WB"
TAG = "v1.1"
NAME = "SENTINEL v1.1 · AI 安全知识情报系统（系统发布）"


def get_token() -> str:
    """从 git 凭证管理器取 GitHub token（不打印）"""
    r = subprocess.run(
        ["git", "credential", "fill"],
        input="protocol=https\nhost=github.com\n",
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    for line in (r.stdout or "").splitlines():
        if line.startswith("password="):
            return line.split("=", 1)[1]
    raise SystemExit("未能获取 GitHub 凭证")


def main() -> None:
    token = get_token()
    h = {"Authorization": f"token {token}", "Accept": "application/vnd.github+json"}

    body = open("发布/release-notes.md", encoding="utf-8").read()

    # ① 创建 Release（已存在则复用 —— 支持失败后重跑）
    print("① 获取/创建 Release v1.0 …", flush=True)
    r = requests.get(
        f"https://api.github.com/repos/{REPO}/releases/tags/{TAG}",
        headers=h,
        timeout=(60, 300),
    )
    if r.status_code == 200:
        rel = r.json()
        print(f"   ✓ 复用已有 Release: {rel['html_url']}", flush=True)
    else:
        r = requests.post(
            f"https://api.github.com/repos/{REPO}/releases",
            headers=h,
            json={"tag_name": TAG, "name": NAME, "body": body, "draft": False, "prerelease": False},
            timeout=(60, 300),
        )
        if r.status_code not in (200, 201):
            print("创建失败:", r.status_code, r.text[:400], flush=True)
            sys.exit(1)
        rel = r.json()
        print(f"   ✓ Release 已创建: {rel['html_url']}", flush=True)
    rid = rel["id"]

    # ② 上传两个 zip
    files = [
        ("发布/Sentinel-系统-完整版-v1.0.zip", "Sentinel-Full-v1.0.zip"),
        ("发布/Sentinel-系统-纯净版-v1.0.zip", "Sentinel-Lite-v1.0.zip"),
    ]
    existing = {a["name"]: a["id"] for a in rel.get("assets", [])}
    for i, (path, name) in enumerate(files, 1):
        size = os.path.getsize(path)
        size_mb = size / 1024 / 1024
        if name in existing:
            # 已存在同名资产 → 删除旧版后上传新版（发布包内容更新时替换，链接不变）
            print(f"② [{i}/{len(files)}] {name} 已存在，删除旧版后上传新版…", flush=True)
            rd = requests.delete(
                f"https://api.github.com/repos/{REPO}/releases/assets/{existing[name]}",
                headers=h,
                timeout=(30, 120),
            )
            if rd.status_code not in (204, 200):
                print(f"   删除旧资产失败: {rd.status_code} {rd.text[:200]}", flush=True)
                sys.exit(1)
        # 重试 3 次：大文件走代理偶发被重置（10054）
        ok = False
        for attempt in range(1, 4):
            print(
                f"② [{i}/{len(files)}] 上传 {name}（{size_mb:.0f} MB，第 {attempt} 次）…",
                flush=True,
            )
            t0 = time.time()
            try:
                with open(path, "rb") as fp:
                    # 上传走**直连**（proxies 显式清空）：
                    # uploads.github.com 可直连，且代理对大流量会 reset（10054）；
                    # API 调用仍走环境代理（api.github.com 直连不通）。
                    r = requests.post(
                        f"https://uploads.github.com/repos/{REPO}/releases/{rid}/assets",
                        headers={
                            **h,
                            "Content-Type": "application/zip",
                            # 显式长度：file object 会被 requests 转成 chunked
                            "Content-Length": str(size),
                        },
                        params={"name": name},
                        data=fp,
                        timeout=(60, 7200),
                        proxies={"http": None, "https": None},
                    )
                if r.status_code in (200, 201):
                    a = r.json()
                    print(
                        f"   ✓ 上传完成（{a['size'] / 1024 / 1024:.0f} MB，"
                        f"耗时 {time.time() - t0:.0f}s）",
                        flush=True,
                    )
                    ok = True
                    break
                print(f"   状态码 {r.status_code}: {r.text[:200]}", flush=True)
            except requests.exceptions.RequestException as e:
                print(f"   连接异常（{type(e).__name__}），10 秒后重试…", flush=True)
            time.sleep(10)
        if not ok:
            print(f"❌ {name} 三次上传均失败", flush=True)
            sys.exit(1)

    # ③ 更新 Release 说明（复用旧 Release 时同步刷新文案）
    print("③ 更新 Release 说明（body）…", flush=True)
    r = requests.patch(
        f"https://api.github.com/repos/{REPO}/releases/{rid}",
        headers=h,
        json={"body": body, "name": NAME},
        timeout=(60, 300),
    )
    print(
        "   ✓ 说明已更新" if r.status_code == 200 else f"   ⚠ 说明更新失败: {r.status_code}",
        flush=True,
    )

    print("\n✅ 全部完成：", flush=True)
    print("   " + rel["html_url"], flush=True)


if __name__ == "__main__":
    main()
