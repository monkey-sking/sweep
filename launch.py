#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Sweep 一键启动器（网页版）。

行为：
- 若 8765 端口已在监听（说明 Sweep 已运行）→ 直接打开浏览器，不重复启动
- 否则以「分离进程」方式后台启动 server.py，等端口就绪后再打开浏览器

双击 start_sweep.bat 即可使用；或命令行 `python launch.py`。
"""
import os
import sys
import time
import socket
import subprocess
import webbrowser

HERE = os.path.dirname(os.path.abspath(__file__))
PY = "python"
SERVER = os.path.join(HERE, "server.py")
URL = "http://127.0.0.1:8765"


def port_open(host, port, timeout=1.0):
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def start_server():
    """以分离进程方式后台启动 server.py，使其不随本启动器退出而关闭。"""
    flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(
        subprocess, "CREATE_NEW_PROCESS_GROUP", 0
    )
    subprocess.Popen(
        [PY, SERVER],
        cwd=HERE,
        creationflags=flags,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
    )


def main():
    if port_open("127.0.0.1", 8765):
        print("Sweep 已在运行 ->", URL)
    else:
        print("正在启动 Sweep ...")
        start_server()
        for _ in range(60):
            if port_open("127.0.0.1", 8765):
                break
            time.sleep(0.5)
        time.sleep(0.4)

    try:
        webbrowser.open(URL)
    except Exception:
        pass
    print("浏览器已打开（若未自动弹出，请手动访问）:", URL)
    # 双击 bat 时让控制台短暂停留，方便看到提示
    if len(sys.argv) == 1:
        time.sleep(2)


if __name__ == "__main__":
    main()
