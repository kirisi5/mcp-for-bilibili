#!/usr/bin/env python3
"""项目初始化脚本：检查环境、创建 .env、安装依赖"""

import sys
import shutil
from pathlib import Path

def main():
    print("=== B站认知 MCP Server 项目初始化 ===\n")

    # 检查 Python 版本
    if sys.version_info < (3, 10):
        print(f"❌ Python 版本需要 >= 3.10，当前: {sys.version}")
        sys.exit(1)
    print(f"✅ Python {sys.version_info.major}.{sys.version_info.minor}")

    # 创建 .env
    env_path = Path(".env")
    env_example = Path(".env.example")
    if not env_path.exists() and env_example.exists():
        shutil.copy(env_example, env_path)
        print(f"✅ 已从 {env_example} 创建 .env，请编辑填入你的 B站 Cookie")
    elif env_path.exists():
        print("⏭  .env 已存在，跳过")

    print("\n请执行以下命令完成安装：")
    print("  pip install -e '.[dev]'")
    print("  # 或者使用 uv:")
    print("  uv sync")

if __name__ == "__main__":
    main()