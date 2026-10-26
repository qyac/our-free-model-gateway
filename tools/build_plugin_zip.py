#!/usr/bin/env python3
"""把 astrbot-plugin/astrbot_plugin_our_free_model 打成可上传的 zip。

AstrBot 的上传校验先解析「归档根目录」，再要求 <根目录>/metadata.yaml 精确存在，
所以包里必须有且只有一个顶层目录，且只放运行时需要的文件。

用法：
    python tools/build_plugin_zip.py                 # 输出到 dist/
    python tools/build_plugin_zip.py --out /tmp/x.zip
"""

from __future__ import annotations

import argparse
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PLUGIN_NAME = "astrbot_plugin_our_free_model"
PLUGIN_DIR = REPO_ROOT / "astrbot-plugin" / PLUGIN_NAME

# 只打运行时需要的文件；tests/、ruff.toml 属于开发期资源，不进上传包。
RUNTIME_FILES = (
    "metadata.yaml",
    "main.py",
    "_conf_schema.json",
    "logo.png",
    "README.md",
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default=None, help="输出 zip 路径，默认 dist/<name>-<version>.zip")
    args = parser.parse_args()

    if not PLUGIN_DIR.is_dir():
        print(f"[失败] 找不到插件目录：{PLUGIN_DIR}")
        return 1

    metadata = (PLUGIN_DIR / "metadata.yaml").read_text(encoding="utf-8")
    version = ""
    for line in metadata.splitlines():
        if line.startswith("version:"):
            version = line.split(":", 1)[1].strip().strip('"').strip("'")
            break
    if version == "":
        print("[失败] metadata.yaml 里没有 version")
        return 1

    out = Path(args.out) if args.out else REPO_ROOT / "dist" / f"{PLUGIN_NAME}-{version}.zip"
    out.parent.mkdir(parents=True, exist_ok=True)

    missing = [name for name in RUNTIME_FILES if not (PLUGIN_DIR / name).is_file()]
    if missing:
        print(f"[失败] 缺少运行时文件：{', '.join(missing)}")
        return 1

    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
        for name in RUNTIME_FILES:
            # 正斜杠 + 顶层目录，AstrBot 的 _resolve_archive_root_dir 才能定位 metadata
            archive.write(PLUGIN_DIR / name, f"{PLUGIN_NAME}/{name}")

    print(f"[完成] {out}  ({out.stat().st_size / 1024:.1f} KB, {len(RUNTIME_FILES)} 个文件)")
    print("       上传前可自查：node tools/verify-plugin-zip.mjs " + str(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
