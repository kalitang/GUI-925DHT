#!/usr/bin/env python3
"""PowerCollector launcher — 直接用 python 调用 main.py，绕开 .bat 编码问题。

行为：
  1. 自检 PySide6 / xlrd / requests；缺则自动 pip install
  2. 切换到本文件所在目录（PowerCollector）
  3. 启动 main.py
"""
import subprocess, sys, os
from pathlib import Path

HERE = Path(__file__).resolve().parent
os.chdir(HERE)

REQUIRED = {"PySide6": "PySide6>=6.6.0", "xlrd": "xlrd>=2.0.0", "requests": "requests>=2.31.0"}

missing = []
for mod in REQUIRED:
    try:
        __import__(mod)
    except ImportError:
        missing.append(REQUIRED[mod])

if missing:
    print(f"[setup] missing: {missing}; installing...", flush=True)
    subprocess.check_call([sys.executable, "-m", "pip", "install", "--disable-pip-version-check", *missing])

print(f"[launch] python -u main.py  (cwd={os.getcwd()})", flush=True)
subprocess.run([sys.executable, "-u", "main.py"], check=False)