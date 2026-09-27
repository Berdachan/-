"""
智能Agent模块
负责：历史批阅智能摘要、邮件语气分发、行为感知推荐、智能助教评分提示、进度预警。

这些Agent优先使用本地历史数据和规则推理，不依赖额外在线服务，便于离线运行和隐私保护。
"""

from __future__ import annotations

import json
import logging
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

import numpy as np
from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg

from config import CACHE_DIR

logger = logging.getLogger(__name__)


# ========== 通用工具 ==========


def _as_datetime(value: Any) -> Optional[datetime]:
    """尽量把数据库/缓存里的时间转成 datetime。"""
    if isinstance(value, datetime):
        return value
    if not value:
        return None
    text = str(value).strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f"):
        try:
            return datetime.strptime(text[:26], fmt)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(text)
    except Exception:
        return None


def normalize_record(record: Any) -> dict[str, Any]:
    """兼容 GradingRecord dataclass 和 dashboard/cache dict。"""
    if isinstance(record, dict):
        return {
            "id": record.get("id"),
            "filename": record.get("filename", "未知文件"),
            "percentage": float(record.get("percentage") or 0),
            "total_score": float(record.get("total_score") or 0),
            "total_max": float(record.get("total_max") or 100),
            "dimensions": record.get("dimensions") or {},
            "strengths": record.get("strengths") or [],
            "weaknesses": record.get("weaknesses") or [],
            "overall_comment": record.get("overall_comment", ""),
            "overall_suggestions": record.get("overall_suggestions", ""),
            "quality_score": record.get("quality_score"),
            "created_at": _as_datetime(record.get("created_at") or record.get("timestamp")),
        }

    return {
        "id": getattr(record, "id", None),
        "filename": getattr(record, "filename", "未知文件"),
        "percentage": float(getattr(record, "percentage", 0) or 0),
        "total_score": float(getattr(record, "total_score", 0) or 0),
        "total_max": float(getattr(record, "total_max", 100) or 100),
        "dimensions": getattr(record, "dimensions", {}) or {},
        "strengths": getattr(record, "strengths", []) or [],
        "weaknesses": getattr(record, "weaknesses", []) or [],
        "overall_comment": getattr(record, "overall_comment", "") or "",
        "overall_suggestions": getattr(record, "overall_suggestions", "") or "",
        "quality_score": getattr(record, "quality_score", None),
        "created_at": _as_datetime(getattr(record, "created_at", None)),
    }


def _dimension_ratio(dim_data: Any) -> float:
    if not isinstance(dim_data, dict):
        return 0.0
    score = float(dim_data.get("score") or 0)
    max_score = float(dim_data.get("max_score") or 1)
    return round(score / max_score * 100, 1) if max_score > 0 else 0.0


# ========== 智能摘要 Agent ==========


class IntelligentSummaryAgent:
    """把历史批阅记录转成月度/学期能力画像和文字总结。"""

    def filter_records(self, records: list[Any], period: str = "all") -> tuple[list[dict[str, Any]], str]:
        normalized = [normalize_record(r) for r in records]
        normalized = [r for r in normalized if r.get("percentage") is not None]
        now = datetime.now()

        if period == "month":
            filtered = [r for r in normalized if r.get("created_at") and r["created_at"].year == now.year and r["created_at"].month == now.month]
            return filtered, f"{now.year}年{now.month}月"

        if period == "semester":
            # 约定：2-7月为春季学期，8-次年1月为秋季学期。
            if 2 <= now.month <= 7:
                start = datetime(now.year, 2, 1)
                end = datetime(now.year, 8, 1)
                label = f"{now.year}年春季学期"
            else:
                start_year = now.year if now.month >= 8 else now.year - 1
                start = datetime(start_year, 8, 1)
                end = datetime(start_year + 1, 2, 1)
                label = f"{start_year}年秋季学期"
            filtered = [r for r in normalized if r.get("created_at") and start <= r["created_at"] < end]
            return filtered, label

        return normalized, "全部历史记录"

    def summarize(self, records: list[Any], period: str = "all") -> dict[str, Any]:
        filtered, period_label = self.filter_records(records, period)
        if not filtered:
            return {
                "period_label": period_label,
                "count": 0,
                "text": f"{period_label}暂无可分析的批阅记录。完成批阅后，系统会自动生成能力雷达图、趋势判断和个性化建议。",
                "dimension_averages": {},
                "alerts": [],
            }

        scores = [float(r["percentage"]) for r in filtered]
        dim_bucket: dict[str, list[float]] = defaultdict(list)
        strength_counter: Counter[str] = Counter()
        weakness_counter: Counter[str] = Counter()

        for record in filtered:
            for dim_name, dim_data in record.get("dimensions", {}).items():
                dim_bucket[dim_name].append(_dimension_ratio(dim_data))
            for item in record.get("strengths", []):
                if item and item != "无":
                    strength_counter[str(item).strip()] += 1
            for item in record.get("weaknesses", []):
                if item and item != "无":
                    weakness_counter[str(item).strip()] += 1

        dim_avg = {name: round(float(np.mean(values)), 1) for name, values in dim_bucket.items() if values}
        strongest_dim = max(dim_avg.items(), key=lambda x: x[1]) if dim_avg else ("暂无", 0)
        weakest_dim = min(dim_avg.items(), key=lambda x: x[1]) if dim_avg else ("暂无", 0)

        sorted_records = sorted(filtered, key=lambda r: r.get("created_at") or datetime.min)
        trend_text = "记录较少，暂不判断趋势"
        if len(sorted_records) >= 2:
            first = sorted_records[0]["percentage"]
            last = sorted_records[-1]["percentage"]
            delta = round(last - first, 1)
            if delta >= 5:
                trend_text = f"整体呈上升趋势，首末记录提升约 {delta} 分。"
            elif delta <= -5:
                trend_text = f"近期表现有下滑迹象，首末记录下降约 {abs(delta)} 分。"
            else:
                trend_text = f"整体较稳定，首末记录变化约 {delta} 分。"

        top_strengths = [s for s, _ in strength_counter.most_common(3)]
        top_weaknesses = [w for w, _ in weakness_counter.most_common(3)]

        avg_score = round(float(np.mean(scores)), 1)
        pass_rate = round(sum(1 for s in scores if s >= 60) / len(scores) * 100, 1)
        excellent_rate = round(sum(1 for s in scores if s >= 85) / len(scores) * 100, 1)

        if avg_score >= 85:
            level_text = "整体实验能力表现优秀，可以进一步加强创新性、误差分析和工程化表达。"
        elif avg_score >= 70:
            level_text = "整体实验能力较稳，建议继续补强薄弱维度，提升分析深度。"
        elif avg_score >= 60:
            level_text = "整体达到基本要求，但波动或短板较明显，建议进行针对性训练。"
        else:
            level_text = "整体风险偏高，建议教师及时介入，优先补齐报告结构、实验步骤和结果分析。"

        alerts = []
        if avg_score < 60:
            alerts.append("平均分低于及格线，需要重点关注。")
        if weakest_dim[1] and weakest_dim[1] < 60:
            alerts.append(f"{weakest_dim[0]} 维度低于 60%，建议单独辅导。")
        if len(sorted_records) >= 3:
            last3 = [r["percentage"] for r in sorted_records[-3:]]
            if last3[0] > last3[1] > last3[2]:
                alerts.append("最近三次成绩连续下降。")

        lines = [
            f"【{period_label}实验能力智能总结】",
            f"共分析 {len(filtered)} 条批阅记录，平均得分率 {avg_score}%，及格率 {pass_rate}%，优秀率 {excellent_rate}%。",
            f"优势维度：{strongest_dim[0]}（{strongest_dim[1]}%）；待提升维度：{weakest_dim[0]}（{weakest_dim[1]}%）。",
            f"趋势判断：{trend_text}",
            f"综合判断：{level_text}",
        ]
        if top_strengths:
            lines.append("高频优点：" + "；".join(top_strengths))
        if top_weaknesses:
            lines.append("高频不足：" + "；".join(top_weaknesses))
        if alerts:
            lines.append("预警提醒：" + "；".join(alerts))
        lines.append(f"建议下一步：围绕“{weakest_dim[0]}”安排一次小练习或面谈反馈，并在下一次批阅中重点观察是否改善。")

        return {
            "period_label": period_label,
            "count": len(filtered),
            "avg_score": avg_score,
            "pass_rate": pass_rate,
            "excellent_rate": excellent_rate,
            "dimension_averages": dim_avg,
            "strongest_dim": strongest_dim,
            "weakest_dim": weakest_dim,
            "top_strengths": top_strengths,
            "top_weaknesses": top_weaknesses,
            "alerts": alerts,
            "text": "\n".join(lines),
            "records": filtered,
        }

    def create_radar_chart(self, parent_widget, summary: dict[str, Any], theme: str = "light") -> Optional[FigureCanvasTkAgg]:
        dim_avg = summary.get("dimension_averages", {})
        if not dim_avg:
            return None

        labels = list(dim_avg.keys())
        values = [float(dim_avg[k]) for k in labels]
        values += values[:1]
        angles = np.linspace(0, 2 * np.pi, len(labels), endpoint=False).tolist()
        angles += angles[:1]

        fig = Figure(figsize=(6.5, 5.5), dpi=100)
        ax = fig.add_subplot(111, projection="polar")
        ax.plot(angles, values, linewidth=2)
        ax.fill(angles, values, alpha=0.18)
        ax.set_xticks(angles[:-1])
        ax.set_xticklabels(labels, fontsize=10)
        ax.set_ylim(0, 100)
        ax.set_yticks([20, 40, 60, 80, 100])
        ax.set_title(f"{summary.get('period_label', '')} 实验能力雷达图", fontsize=13, pad=18)
        ax.grid(True, alpha=0.35)

        if theme == "dark":
            fig.patch.set_facecolor("#1e293b")
            ax.set_facecolor("#334155")
            ax.tick_params(colors="white")
            ax.title.set_color("white")
            for label in ax.get_xticklabels() + ax.get_yticklabels():
                label.set_color("white")

        fig.tight_layout()
        canvas = FigureCanvasTkAgg(fig, master=parent_widget)
        canvas.draw()
        return canvas


# ========== 邮件智能分发 Agent ==========


class EmailDispatchAgent:
    """根据成绩、质量和紧急程度生成更有温度的邮件主题与正文。"""

    def infer_urgency(self, score_data: dict[str, Any], quality_score: Optional[float] = None) -> str:
        percentage = float(score_data.get("percentage") or 0)
        if percentage < 60:
            return "high"
        if quality_score is not None and quality_score < 60:
            return "medium"
        if percentage < 75:
            return "medium"
        return "normal"

    def compose(self,
                recipient_name: str,
                score_data: dict[str, Any],
                attachment_label: str = "批阅结果",
                custom_body: str = "",
                quality_score: Optional[float] = None,
                subject: Optional[str] = None) -> dict[str, str]:
        percentage = float(score_data.get("percentage") or 0)
        urgency = self.infer_urgency(score_data, quality_score)
        strengths = [str(x) for x in score_data.get("strengths", []) if str(x).strip() and str(x).strip() != "无"]
        weaknesses = [str(x) for x in score_data.get("weaknesses", []) if str(x).strip() and str(x).strip() != "无"]
        suggestions = str(score_data.get("overall_suggestions") or "建议结合附件中的维度评语继续完善。")

        if urgency == "high":
            tone_intro = (
                f"这次报告得分率为 {percentage:.1f}%，目前还没有达到及格线。请不要灰心，这通常意味着报告结构、步骤说明或结果分析中有几个关键点需要补齐。"
            )
            care = "建议你先按照附件中的维度建议逐项修改，也欢迎带着问题来沟通，我们可以一起把薄弱环节拆开解决。"
            subject = subject or f"实验报告反馈：请重点查看改进建议（{percentage:.1f}%）"
        elif urgency == "medium":
            tone_intro = f"这次报告得分率为 {percentage:.1f}%，已经具备一定基础，但仍有进一步提升空间。"
            care = "建议优先处理附件中标出的薄弱维度，再补充必要的数据解释和结论反思。"
            subject = subject or f"实验报告反馈：请查看本次改进建议（{percentage:.1f}%）"
        else:
            tone_intro = f"这次报告得分率为 {percentage:.1f}%，整体完成情况不错。"
            care = "可以继续保持，同时尝试在误差分析、结果解释或创新思考上写得更深入。"
            subject = subject or f"实验报告批阅结果（{percentage:.1f}%）"

        lines = [
            f"尊敬的{recipient_name}：",
            "",
            "您好！",
            "",
            tone_intro,
        ]
        if strengths:
            lines.extend(["", "本次值得肯定的地方：", "- " + "\n- ".join(strengths[:3])])
        if weaknesses:
            lines.extend(["", "建议重点关注：", "- " + "\n- ".join(weaknesses[:3])])
        lines.extend([
            "",
            "下一步建议：",
            suggestions,
            "",
            care,
            "",
            f"附件中包含完整的{attachment_label}，请查收。",
        ])
        if custom_body:
            lines.extend(["", "补充说明：", custom_body.strip()])
        lines.extend(["", "祝好！", "实验报告批阅智能体"])

        return {
            "subject": subject,
            "body": "\n".join(lines),
            "urgency": urgency,
        }


# ========== 行为感知 Agent ==========


@dataclass
class QuickAction:
    action_key: str
    title: str
    reason: str


class BehaviorAwareAgent:
    """记录用户行为，并基于常用功能推荐快捷入口和下一步操作。"""

    ACTION_LABELS = {
        "login": "登录系统",
        "import_reports": "导入报告",
        "import_folder": "导入文件夹",
        "start_grading": "开始批阅",
        "export_excel": "导出Excel",
        "export_pdf": "导出PDF",
        "export_ppt": "导出PPT",
        "export_docx": "导出Word",
        "email": "发送邮件",
        "history": "查看历史",
        "dashboard": "数据仪表盘",
        "summary_agent": "智能摘要",
        "profile": "个人资料",
    }

    def _file(self, user_id: int) -> Path:
        return CACHE_DIR / f"behavior_events_user_{user_id}.json"

    def _load(self, user_id: int) -> list[dict[str, Any]]:
        path = self._file(user_id)
        if path.exists():
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, list):
                    return data
            except Exception as e:
                logger.warning(f"加载行为记录失败: {e}")
        return []

    def _save(self, user_id: int, events: list[dict[str, Any]]) -> None:
        try:
            path = self._file(user_id)
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(events[-500:], f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.warning(f"保存行为记录失败: {e}")

    def log_event(self, user_id: Optional[int], event_type: str, metadata: Optional[dict[str, Any]] = None) -> None:
        if not user_id:
            return
        events = self._load(user_id)
        now = datetime.now()
        events.append({
            "event_type": event_type,
            "label": self.ACTION_LABELS.get(event_type, event_type),
            "timestamp": now.isoformat(timespec="seconds"),
            "hour": now.hour,
            "weekday": now.weekday(),
            "metadata": metadata or {},
        })
        self._save(user_id, events)

    def analyze(self, user_id: int) -> dict[str, Any]:
        events = self._load(user_id)
        if not events:
            return {
                "events": [],
                "summary": "暂无行为记录。继续使用后，系统会自动学习你的常用功能并推荐快捷入口。",
                "quick_actions": [
                    QuickAction("import_reports", "导入报告", "新用户最常用的起点"),
                    QuickAction("history", "查看历史", "快速回看已有批阅记录"),
                    QuickAction("summary_agent", "智能摘要", "生成能力雷达图和文字总结"),
                ],
                "preload_hint": "建议预加载：OCR处理器、历史记录摘要缓存。",
            }

        counter = Counter(e.get("event_type") for e in events)
        hour_counter = Counter(int(e.get("hour", 0)) for e in events)
        favorite_hour = hour_counter.most_common(1)[0][0]
        most_common = counter.most_common(5)

        quick_actions: list[QuickAction] = []
        for event_type, count in most_common:
            if event_type == "login":
                continue
            quick_actions.append(QuickAction(
                event_type,
                self.ACTION_LABELS.get(event_type, str(event_type)),
                f"最近使用 {count} 次，属于高频操作",
            ))

        last_event = events[-1].get("event_type")
        if last_event in {"import_reports", "import_folder"}:
            quick_actions.insert(0, QuickAction("start_grading", "开始批阅", "刚导入报告，下一步通常是开始批阅"))
            preload_hint = "建议预加载：评分风格、OCR处理器、LLM评分队列。"
        elif last_event == "start_grading":
            quick_actions.insert(0, QuickAction("email", "发送邮件", "批阅完成后通常需要分发反馈"))
            preload_hint = "建议预加载：导出模板、SMTP配置、邮件语气模板。"
        elif last_event in {"export_excel", "export_pdf", "export_ppt", "export_docx"}:
            quick_actions.insert(0, QuickAction("email", "发送邮件", "导出后通常需要发送附件"))
            preload_hint = "建议预加载：SMTP配置和最近收件人输入框。"
        else:
            preload_hint = "建议预加载：历史记录、仪表盘统计和智能摘要缓存。"

        # 去重并限制数量
        seen = set()
        deduped = []
        for item in quick_actions:
            if item.action_key not in seen:
                seen.add(item.action_key)
                deduped.append(item)
            if len(deduped) >= 5:
                break

        summary = (
            f"已记录 {len(events)} 次操作。你最常使用的功能是："
            + "、".join(self.ACTION_LABELS.get(k, str(k)) for k, _ in most_common[:3])
            + f"。常见登录/操作时段约为 {favorite_hour}:00 前后。"
        )
        return {
            "events": events,
            "summary": summary,
            "quick_actions": deduped,
            "preload_hint": preload_hint,
            "top_actions": most_common,
            "favorite_hour": favorite_hour,
        }


# ========== 智能助教 Agent ==========


class TeachingAssistantAgent:
    """根据个人资料构造评分侧重点和评语风格提示。"""

    def build_profile_hint(self, user: Any) -> str:
        if not user:
            return ""
        major = getattr(user, "major", "") or ""
        grade = getattr(user, "grade", "") or ""
        course = getattr(user, "experiment_course", "") or ""
        nickname = getattr(user, "nickname", "") or getattr(user, "username", "") or ""

        parts = []
        if nickname:
            parts.append(f"当前用户/班级对象：{nickname}")
        if major:
            parts.append(f"专业：{major}")
        if grade:
            parts.append(f"年级：{grade}")
        if course:
            parts.append(f"实验课程：{course}")

        if not parts:
            return ""

        grade_hint = ""
        grade_text = grade.lower()
        if any(key in grade_text for key in ["大一", "一年级", "2026", "2025"]):
            grade_hint = "低年级学生请更关注报告格式规范、实验目的是否清楚、步骤是否完整，并用鼓励式语言指出基础问题。"
        elif any(key in grade_text for key in ["大二", "二年级"]):
            grade_hint = "二年级学生请在规范性之外加强对实验现象、数据处理和结果解释的要求。"
        elif any(key in grade_text for key in ["大三", "三年级", "大四", "四年级", "研究生"]):
            grade_hint = "高年级学生请更关注创新性、工程化思维、误差分析、复杂问题解释和自主改进能力。"

        course_hint = ""
        if course:
            if "程序" in course or "编程" in course or "算法" in course:
                course_hint = "课程侧重点：关注代码逻辑、算法复杂度、测试覆盖和运行结果分析。"
            elif "电路" in course or "物理" in course:
                course_hint = "课程侧重点：关注实验数据记录、误差来源、仪器使用和现象解释。"
            elif "数据库" in course:
                course_hint = "课程侧重点：关注数据模型、SQL正确性、约束设计和结果验证。"

        final = ["\n## 智能助教Agent补充要求"]
        final.append("；".join(parts))
        final.append("请结合以上背景调整评分侧重点和评语风格。")
        if grade_hint:
            final.append(grade_hint)
        if course_hint:
            final.append(course_hint)
        return "\n".join(final)

    def describe_profile(self, user: Any) -> str:
        hint = self.build_profile_hint(user)
        if not hint:
            return "个人资料中尚未填写专业、年级或实验课程。完善后，智能助教Agent会自动调整评分侧重点。"
        return hint.replace("\n## 智能助教Agent补充要求\n", "")


# ========== 进度预警 Agent ==========


class ProgressWarningAgent:
    """监测提交频率和成绩趋势，提醒教师关注可能困难学生。"""

    def analyze(self, records: list[Any], user: Any = None) -> dict[str, Any]:
        normalized = [normalize_record(r) for r in records]
        normalized = [r for r in normalized if r.get("created_at")]
        normalized.sort(key=lambda r: r["created_at"])

        if not normalized:
            return {
                "alerts": ["暂无可分析的提交记录。"],
                "level": "info",
                "text": "暂无可分析的提交记录。学生完成几次提交后，系统会自动判断提交频率、成绩趋势和薄弱维度。",
            }

        scores = [r["percentage"] for r in normalized]
        alerts = []
        level = "normal"

        avg_score = round(float(np.mean(scores)), 1)
        if avg_score < 60:
            alerts.append(f"平均得分率为 {avg_score}%，低于及格线。")
            level = "high"
        elif avg_score < 70:
            alerts.append(f"平均得分率为 {avg_score}%，处于临界区间。")
            level = "medium"

        if len(scores) >= 3:
            last3 = scores[-3:]
            if last3[0] > last3[1] > last3[2]:
                alerts.append(f"最近三次成绩连续下降：{last3[0]:.1f}% → {last3[1]:.1f}% → {last3[2]:.1f}%。")
                level = "high"
            elif last3[-1] - last3[0] <= -8:
                alerts.append("近三次成绩下滑超过 8 分。")
                level = "medium" if level != "high" else level

        last_date = normalized[-1]["created_at"]
        days_since_last = (datetime.now() - last_date).days
        if days_since_last >= 21:
            alerts.append(f"距离最近一次提交已 {days_since_last} 天，可能存在提交中断。")
            level = "high"
        elif days_since_last >= 14:
            alerts.append(f"距离最近一次提交已 {days_since_last} 天，建议提醒学生跟进。")
            level = "medium" if level != "high" else level

        # 最近维度短板
        recent = normalized[-min(5, len(normalized)):]
        dim_bucket: dict[str, list[float]] = defaultdict(list)
        for record in recent:
            for dim_name, dim_data in record.get("dimensions", {}).items():
                dim_bucket[dim_name].append(_dimension_ratio(dim_data))
        weak_dims = [(name, round(float(np.mean(values)), 1)) for name, values in dim_bucket.items() if values and np.mean(values) < 65]
        weak_dims.sort(key=lambda x: x[1])
        if weak_dims:
            alerts.append("近期薄弱维度：" + "、".join(f"{n}({v}%)" for n, v in weak_dims[:3]))
            if level == "normal":
                level = "medium"

        if not alerts:
            alerts.append("目前未发现明显风险，成绩和提交节奏较稳定。")

        name = getattr(user, "nickname", None) or getattr(user, "username", "该学生") if user else "该学生"
        advice = {
            "high": "建议尽快安排一次单独沟通，明确本周可完成的一项小目标，并给出样例或修改清单。",
            "medium": "建议在下次课或下次反馈中重点提醒薄弱维度，并观察是否改善。",
            "normal": "建议保持当前节奏，鼓励学生继续提升分析深度与表达质量。",
            "info": "建议先积累更多提交记录。",
        }.get(level, "建议持续观察。")

        text = f"【进度预警Agent】\n对象：{name}\n风险等级：{level}\n" + "\n".join(f"- {a}" for a in alerts) + f"\n\n处理建议：{advice}"
        return {
            "alerts": alerts,
            "level": level,
            "text": text,
            "avg_score": avg_score,
            "days_since_last": days_since_last,
        }
