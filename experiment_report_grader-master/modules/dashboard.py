"""
数据可视化仪表盘模块
负责：评分统计、趋势分析、图表生成（matplotlib嵌入tkinter）
"""
import os
import logging
from typing import Optional
from collections import defaultdict

import numpy as np
from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
#import matplotlib.pyplot as plt

logger = logging.getLogger(__name__)


# ========== 全局字体配置 ==========
def _setup_chinese_font():
    """配置matplotlib中文字体"""
    import logging as py_logging
    # 关闭 matplotlib 字体查找日志
    py_logging.getLogger('matplotlib.font_manager').setLevel(py_logging.ERROR)

    import matplotlib
    from matplotlib import font_manager
    
    # 尝试查找系统中文字体
    chinese_fonts = [
        'SimHei',           # 黑体
        'Microsoft YaHei',  # 微软雅黑
        'SimSun',           # 宋体
        'WenQuanYi Micro Hei',  # Linux
        'Noto Sans CJK SC',   # Linux
        'PingFang SC',        # macOS
        'Heiti SC',           # macOS
    ]
    
    available_fonts = [f.name for f in font_manager.fontManager.ttflist]
    
    found_font = None
    for font_name in chinese_fonts:
        if font_name in available_fonts:
            found_font = font_name
            break
    
    if found_font:
        # 显式设置字重为 normal，避免 matplotlib 查找 bold 失败
        matplotlib.rcParams['font.family'] = ['sans-serif']
        matplotlib.rcParams['font.sans-serif'] = [found_font] + matplotlib.rcParams.get('font.sans-serif', [])
        matplotlib.rcParams['font.weight'] = 'normal'
        matplotlib.rcParams['axes.unicode_minus'] = False
        # 关闭字体字重警告
        matplotlib.rcParams['pdf.fonttype'] = 42
        logger.info(f"matplotlib中文字体已配置: {found_font}")
    else:
        # 如果没有找到中文字体，尝试从系统路径加载
        import platform
        system = platform.system()
        
        font_paths = []
        if system == 'Windows':
            font_paths = [
            r'C:\Windows\Fonts\simhei.ttf',      # 黑体 .ttf
            r'C:\Windows\Fonts\simsunb.ttf',      # 宋体粗体 .ttf
            r'C:\Windows\Fonts\simfang.ttf',      # 仿宋 .ttf
            r'C:\Windows\Fonts\simkai.ttf',     # 楷体 .ttf
            r'C:\Windows\Fonts\msyh.ttc',         # 微软雅黑（fallback）
            r'C:\Windows\Fonts\simsun.ttc',       # 宋体（fallback）
            ]
        elif system == 'Linux':
            font_paths = [
                '/usr/share/fonts/truetype/wqy/wqy-microhei.ttc',
                '/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc',
            ]
        elif system == 'Darwin':  # macOS
            font_paths = [
                '/System/Library/Fonts/PingFang.ttc',
                '/Library/Fonts/Arial Unicode.ttf',
            ]
        
        for fp in font_paths:
            if os.path.exists(fp):
                try:
                    font_manager.fontManager.addfont(fp)
                    prop = font_manager.FontProperties(fname=fp)
                    matplotlib.rcParams['font.family'] = ['sans-serif']
                    matplotlib.rcParams['font.sans-serif'] = [prop.get_name()] + matplotlib.rcParams.get('font.sans-serif', [])
                    matplotlib.rcParams['font.weight'] = 'normal'
                    matplotlib.rcParams['axes.unicode_minus'] = False
                    matplotlib.rcParams['pdf.fonttype'] = 42
                    logger.info(f"matplotlib中文字体已加载: {fp}")
                    found_font = prop.get_name()
                    break
                except Exception as e:
                    logger.warning(f"加载字体失败 {fp}: {e}")
        
        if not found_font:
            logger.warning("未找到中文字体，图表中文将显示为方框")

# 模块导入时立即配置字体
_setup_chinese_font()


class GradingDashboard:
    """评分数据仪表盘"""

    def __init__(self):
        self.history = []  # 历史评分记录
        self._load_history()

    def _load_history(self):
        """加载历史记录"""
        from config import CACHE_DIR
        import json
        history_file = CACHE_DIR / "grading_history.json"
        if history_file.exists():
            try:
                with open(history_file, 'r', encoding='utf-8') as f:
                    self.history = json.load(f)
            except Exception as e:
                logger.warning(f"加载历史记录失败: {e}")

    def _save_history(self):
        """保存历史记录"""
        from config import CACHE_DIR
        import json
        try:
            history_file = CACHE_DIR / "grading_history.json"
            history_file.parent.mkdir(parents=True, exist_ok=True)
            with open(history_file, 'w', encoding='utf-8') as f:
                json.dump(self.history, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.warning(f"保存历史记录失败: {e}")

    def add_record(self, filename: str, score_data: dict, timestamp: Optional[str] = None):
        """添加评分记录"""
        from datetime import datetime
        record = {
            "filename": filename,
            "total_score": score_data.get("total_score", 0),
            "total_max": score_data.get("total_max", 100),
            "percentage": score_data.get("percentage", 0),
            "dimensions": score_data.get("dimensions", {}),
            "timestamp": timestamp or datetime.now().isoformat(),
        }
        self.history.append(record)
        self._save_history()

    def get_statistics(self) -> dict:
        """获取统计数据"""
        if not self.history:
            return {}

        scores = [r["percentage"] for r in self.history]

        stats = {
            "total_reports": len(self.history),
            "avg_score": round(np.mean(scores), 1) if scores else 0,
            "median_score": round(np.median(scores), 1) if scores else 0,
            "min_score": round(min(scores), 1) if scores else 0,
            "max_score": round(max(scores), 1) if scores else 0,
            "std_dev": round(np.std(scores), 1) if scores else 0,
            "pass_rate": round(sum(1 for s in scores if s >= 60) / len(scores) * 100, 1) if scores else 0,
            "excellent_rate": round(sum(1 for s in scores if s >= 85) / len(scores) * 100, 1) if scores else 0,
        }

        # 各维度统计
        dim_stats = defaultdict(list)
        for record in self.history:
            for dim_name, dim_data in record.get("dimensions", {}).items():
                if isinstance(dim_data, dict):
                    score = dim_data.get("score", 0)
                    max_score = dim_data.get("max_score", 1)
                    ratio = score / max_score * 100 if max_score > 0 else 0
                    dim_stats[dim_name].append(ratio)

        stats["dimension_averages"] = {
            name: round(np.mean(values), 1)
            for name, values in dim_stats.items()
        }

        # 分数段分布
        score_ranges = {
            "优秀 (90-100)": 0,
            "良好 (80-89)": 0,
            "中等 (70-79)": 0,
            "及格 (60-69)": 0,
            "不及格 (<60)": 0,
        }
        for s in scores:
            if s >= 90:
                score_ranges["优秀 (90-100)"] += 1
            elif s >= 80:
                score_ranges["良好 (80-89)"] += 1
            elif s >= 70:
                score_ranges["中等 (70-79)"] += 1
            elif s >= 60:
                score_ranges["及格 (60-69)"] += 1
            else:
                score_ranges["不及格 (<60)"] += 1

        stats["score_distribution"] = score_ranges

        return stats

    def _apply_dark_theme(self, fig, ax):
        """应用深色主题"""
        fig.patch.set_facecolor('#1e293b')
        ax.set_facecolor('#334155')
        ax.tick_params(colors='white')
        ax.xaxis.label.set_color('white')
        ax.yaxis.label.set_color('white')
        ax.title.set_color('white')
        for spine in ax.spines.values():
            spine.set_color('#475569')

    def create_score_distribution_chart(self, parent_widget, theme="light") -> Optional[FigureCanvasTkAgg]:
        """创建分数分布柱状图"""
        stats = self.get_statistics()
        if not stats:
            return None

        fig = Figure(figsize=(8, 5), dpi=100)
        ax = fig.add_subplot(111)

        distribution = stats.get("score_distribution", {})
        labels = list(distribution.keys())
        values = list(distribution.values())

        colors = ["#22c55e", "#84cc16", "#eab308", "#f97316", "#ef4444"]

        bars = ax.bar(labels, values, color=colors, edgecolor='white', linewidth=1.5)

        for bar, val in zip(bars, values):
            height = bar.get_height()
            ax.text(bar.get_x() + bar.get_width()/2., height,
                   f'{int(val)}',
                   ha='center', va='bottom', fontsize=11, fontweight='bold')

        ax.set_ylabel('报告数量', fontsize=12)
        ax.set_title('评分分布统计', fontsize=14, fontweight='bold', pad=15)
        ax.set_ylim(0, max(values) * 1.2 if values else 10)

        if theme == "dark":
            self._apply_dark_theme(fig, ax)

        fig.tight_layout()

        canvas = FigureCanvasTkAgg(fig, master=parent_widget)
        canvas.draw()
        return canvas

    def create_dimension_radar_chart(self, parent_widget, theme="light") -> Optional[FigureCanvasTkAgg]:
        """创建维度雷达图"""
        stats = self.get_statistics()
        if not stats or "dimension_averages" not in stats:
            return None

        fig = Figure(figsize=(7, 7), dpi=100)
        ax = fig.add_subplot(111, projection='polar')

        dim_data = stats["dimension_averages"]
        categories = list(dim_data.keys())
        values = list(dim_data.values())

        values += values[:1]
        angles = np.linspace(0, 2 * np.pi, len(categories), endpoint=False).tolist()
        angles += angles[:1]

        ax.plot(angles, values, 'o-', linewidth=2, color='#2563eb', label='平均分')
        ax.fill(angles, values, alpha=0.25, color='#2563eb')

        ax.set_xticks(angles[:-1])
        ax.set_xticklabels(categories, fontsize=10)
        ax.set_ylim(0, 100)
        ax.set_yticks([20, 40, 60, 80, 100])
        ax.set_yticklabels(['20', '40', '60', '80', '100'], fontsize=8)
        ax.grid(True, linestyle='--', alpha=0.5)

        ax.set_title('各维度评分雷达图', fontsize=14, fontweight='bold', pad=20)

        if theme == "dark":
            fig.patch.set_facecolor('#1e293b')
            ax.set_facecolor('#334155')
            ax.tick_params(colors='white')
            ax.title.set_color('white')

        fig.tight_layout()

        canvas = FigureCanvasTkAgg(fig, master=parent_widget)
        canvas.draw()
        return canvas

    def create_trend_chart(self, parent_widget, theme="light") -> Optional[FigureCanvasTkAgg]:
        """创建评分趋势图"""
        if len(self.history) < 2:
            return None

        fig = Figure(figsize=(10, 5), dpi=100)
        ax = fig.add_subplot(111)

        sorted_history = sorted(self.history, key=lambda x: x.get("timestamp", ""))

        x = list(range(1, len(sorted_history) + 1))
        y = [r["percentage"] for r in sorted_history]

        # 修复：使用 len() 判断数组长度，而不是直接用 if
        ma = None
        ma_x: list[int] = []
        if len(y) >= 3:
            window = min(3, len(y))
            ma = np.convolve(y, np.ones(window)/window, mode='valid')
            ma_x = list(range(window, len(y) + 1))

        ax.plot(x, y, 'o-', color='#3b82f6', linewidth=1.5, markersize=6, label='单次评分', alpha=0.7)
        
        # 修复：使用 len() 判断
        if ma is not None and len(ma) > 0:
            ax.plot(ma_x, ma, '--', color='#ef4444', linewidth=2, label=f'{window}次移动平均')

        avg = np.mean(y)
        ax.axhline(y=avg, color='#22c55e', linestyle='-.', linewidth=1.5, label=f'平均分: {avg:.1f}')

        ax.set_xlabel('报告序号', fontsize=12)
        ax.set_ylabel('得分率 (%)', fontsize=12)
        ax.set_title('评分趋势分析', fontsize=14, fontweight='bold', pad=15)
        ax.legend(loc='best', fontsize=10)
        ax.set_ylim(0, 105)
        ax.grid(True, alpha=0.3)

        if theme == "dark":
            self._apply_dark_theme(fig, ax)
            legend = ax.legend(loc='best', fontsize=10, facecolor='#334155', edgecolor='#475569')
            if legend:
                for text in legend.get_texts():
                    text.set_color('white')

        fig.tight_layout()

        canvas = FigureCanvasTkAgg(fig, master=parent_widget)
        canvas.draw()
        return canvas

    def create_comparison_chart(self, parent_widget, reports_data: list, theme="light") -> Optional[FigureCanvasTkAgg]:
        """创建批量对比柱状图"""
        if not reports_data:
            return None

        fig = Figure(figsize=(10, 6), dpi=100)
        ax = fig.add_subplot(111)

        names = [r.get("filename", f"报告{i+1}")[:15] for i, r in enumerate(reports_data)]
        scores = [r.get("percentage", 0) for r in reports_data]

        bar_colors = []
        for s in scores:
            if s >= 90:
                bar_colors.append("#22c55e")
            elif s >= 80:
                bar_colors.append("#84cc16")
            elif s >= 70:
                bar_colors.append("#eab308")
            elif s >= 60:
                bar_colors.append("#f97316")
            else:
                bar_colors.append("#ef4444")

        bars = ax.barh(names, scores, color=bar_colors, edgecolor='white', height=0.6)

        for bar, val in zip(bars, scores):
            width = bar.get_width()
            ax.text(width + 1, bar.get_y() + bar.get_height()/2.,
                   f'{val:.1f}%',
                   ha='left', va='center', fontsize=10, fontweight='bold')

        ax.set_xlabel('得分率 (%)', fontsize=12)
        ax.set_title('批量评分对比', fontsize=14, fontweight='bold', pad=15)
        ax.set_xlim(0, 110)
        ax.axvline(x=60, color='red', linestyle='--', alpha=0.5, label='及格线')
        ax.axvline(x=85, color='green', linestyle='--', alpha=0.5, label='优秀线')
        ax.legend(loc='lower right', fontsize=9)
        ax.grid(True, axis='x', alpha=0.3)

        if theme == "dark":
            self._apply_dark_theme(fig, ax)
            legend = ax.legend(loc='lower right', fontsize=9, facecolor='#334155', edgecolor='#475569')
            if legend:
                for text in legend.get_texts():
                    text.set_color('white')

        fig.tight_layout()

        canvas = FigureCanvasTkAgg(fig, master=parent_widget)
        canvas.draw()
        return canvas

    def clear_history(self):
        """清空历史记录"""
        self.history = []
        self._save_history()
        logger.info("历史记录已清空")