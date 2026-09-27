"""
全局配置文件
管理路径、API密钥、评分标准、缓存、主题等
"""

import os  # noqa: F401  # 用于 os.getenv
from pathlib import Path

# ========== 项目根目录 ==========
BASE_DIR = Path(__file__).parent.resolve()

# ========== 数据路径 ==========
DATA_DIR = BASE_DIR / "data"
INPUT_DIR = DATA_DIR / "input"
OUTPUT_DIR = DATA_DIR / "output"

INPUT_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# ========== 缓存路径 ==========
CACHE_DIR = BASE_DIR / ".cache"
CACHE_DIR.mkdir(exist_ok=True)
CACHE_FILE = CACHE_DIR / "grading_cache.json"
RECENT_FILES = CACHE_DIR / "recent_files.json"

# ========== Tesseract OCR 路径 ==========
TESSERACT_CMD = r"D:\tesseract\tesseract.exe"

# ========== Kimi API 配置 ==========
KIMI_API_KEY = "sk-16NqdwvnKiIL5zWP3tLba15906xI6xHU48sVSBaG8vG7zPVK"
KIMI_BASE_URL = "https://api.moonshot.cn/v1"
KIMI_MODEL = "moonshot-v1-128k"

# ========== 评分标准 ==========
GRADING_CRITERIA = {
    "实验目的理解": {"description": "是否准确理解实验目的，目标表述是否清晰", "max_score": 20},
    "实验步骤完整性": {
        "description": "实验步骤是否完整、逻辑是否清晰、操作是否规范",
        "max_score": 30,
    },
    "结果分析与数据处理": {
        "description": "数据记录是否准确，分析是否合理，图表是否规范",
        "max_score": 30,
    },
    "结论与总结": {"description": "结论是否明确，总结是否到位，反思是否有深度", "max_score": 20},
}

TOTAL_SCORE = sum(c["max_score"] for c in GRADING_CRITERIA.values())

# ========== 文件格式支持 ==========
SUPPORTED_IMAGE_FORMATS = (".png", ".jpg", ".jpeg", ".bmp", ".tiff", ".webp", ".gif")
SUPPORTED_DOC_FORMATS = (".pdf", ".docx", ".doc")
ALL_SUPPORTED_FORMATS = SUPPORTED_IMAGE_FORMATS + SUPPORTED_DOC_FORMATS

# ========== OCR 大文件优化配置 ==========
OCR_MAX_PAGES = 50
OCR_DEFAULT_DPI = 200
OCR_MAX_DPI = 300

# ========== 导出配置 ==========
EXCEL_OUTPUT_NAME = "grading_results.xlsx"
PDF_OUTPUT_NAME = "grading_report.pdf"

# ========== GUI 主题配置（保守方案：确保所有文字清晰可读）==========
# 核心原则：
# 1. 所有文字都用黑色或深灰色，确保对比度
# 2. 背景用浅色或中性色
# 3. 强调色仅用于装饰性元素（边框、选中高亮），不用于文字

THEMES = {
    "light": {
        "name": "浅色",
        # 基础
        "bg": "#f5f5f5",
        "fg": "#000000",
        # 强调色（仅用于装饰，不用于文字）
        "accent": "#2563eb",
        "accent_hover": "#1d4ed8",
        # 卡片/面板
        "card_bg": "#ffffff",
        "border": "#cccccc",
        # 状态色
        "success": "#15803d",
        "warning": "#b45309",
        "error": "#dc2626",
        # Treeview - 白底黑字，选中用浅蓝底深蓝字
        "tree_bg": "#ffffff",
        "tree_fg": "#000000",
        "tree_select_bg": "#dbeafe",
        "tree_select_fg": "#1e3a8a",
        # 文本框
        "text_bg": "#ffffff",
        "text_fg": "#000000",
        "text_select_bg": "#bfdbfe",
        # 按钮 - 白底黑字，边框用强调色
        "button_bg": "#ffffff",
        "button_fg": "#000000",
        "button_active_bg": "#e5e7eb",
        # 框架/标签 - 全部黑字
        "frame_bg": "#f5f5f5",
        "label_fg": "#000000",
        "labelframe_fg": "#000000",
        # 日志颜色
        "log_info": "#374151",
        "log_success": "#15803d",
        "log_warning": "#b45309",
        "log_error": "#dc2626",
        # 进度条
        "progress_bg": "#e5e7eb",
        "progress_fg": "#2563eb",
    },
    "dark": {
        "name": "深色",
        # 基础 - 深蓝灰底，白字
        "bg": "#1e293b",
        "fg": "#ffffff",
        # 强调色
        "accent": "#38bdf8",
        "accent_hover": "#7dd3fc",
        # 卡片/面板 - 比背景稍亮
        "card_bg": "#334155",
        "border": "#475569",
        # 状态色（更亮确保可见）
        "success": "#4ade80",
        "warning": "#fbbf24",
        "error": "#f87171",
        # Treeview - 卡片色底白字，选中用更深色+亮强调色
        "tree_bg": "#334155",
        "tree_fg": "#ffffff",
        "tree_select_bg": "#1e293b",
        "tree_select_fg": "#38bdf8",
        # 文本框 - 比卡片更深
        "text_bg": "#0f172a",
        "text_fg": "#e2e8f0",
        "text_select_bg": "#334155",
        # 按钮 - 卡片色底白字
        "button_bg": "#334155",
        "button_fg": "#ffffff",
        "button_active_bg": "#475569",
        # 框架/标签 - 全部白字
        "frame_bg": "#1e293b",
        "label_fg": "#ffffff",
        "labelframe_fg": "#38bdf8",
        # 日志颜色
        "log_info": "#cbd5e1",
        "log_success": "#4ade80",
        "log_warning": "#fbbf24",
        "log_error": "#f87171",
        # 进度条
        "progress_bg": "#334155",
        "progress_fg": "#38bdf8",
    }
}

DEFAULT_THEME = "light"
MAX_RECENT_FILES = 10

# ========== MySQL 数据库配置 ==========
# 请根据你的 MySQL 环境修改以下配置
DB_CONFIG = {
    "host": "localhost",
    "port": 3306,
    "user": "root",           # 修改为你的MySQL用户名
    "password": "Liusai3241646880",  # 修改为你的MySQL密码
    "database": "experiment_grader",  # 数据库名，会自动创建
    "charset": "utf8mb4",
    "collation": "utf8mb4_unicode_ci",
    "autocommit": False,
    "raise_on_warnings": False, 
}