"""
实验报告批阅智能体 - 主入口（优化版）
"""

import sys
import logging
from pathlib import Path
import tkinter as tk

# Ensure local project path is first so local modules can be imported
sys.path.insert(0, str(Path(__file__).parent))

from modules.gui_app import GraderGUI

# 配置日志
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
    ]
)


def main():
    """主函数"""
    print("=" * 50)
    print("  实验报告批阅智能体 v4.0")
    print("=" * 50)
    print("启动GUI界面...")
    print("快捷键: Ctrl+O导入, Ctrl+F导入文件夹, Ctrl+G批阅, Ctrl+S停止")
    print("         Ctrl+R重新批阅, Ctrl+E导出Excel, Ctrl+P导出PDF, Ctrl+T切换主题")

    root = tk.Tk()

    # 设置DPI感知（Windows高清屏）- 在创建窗口后、启用拖拽前设置
    try:
        from ctypes import windll
        windll.shcore.SetProcessDpiAwareness(1)
    except (ImportError, AttributeError, OSError):
        pass

    app = GraderGUI(root)
    app.run()


if __name__ == "__main__":
    main()