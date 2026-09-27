"""
LLM评分模块（v3.1 - 串行队列 + 智能退避）
负责：构建评分链、调用Kimi API、生成评语、缓存、速率控制
"""

import json
import logging
import time
import hashlib
import threading
from typing import Optional, Callable
from pathlib import Path
from queue import Queue, Empty
from dataclasses import dataclass
from pydantic import BaseModel, Field, SecretStr

from langchain_openai import ChatOpenAI
from langchain.prompts import PromptTemplate

from config import (
    KIMI_API_KEY, KIMI_BASE_URL, KIMI_MODEL,
    GRADING_CRITERIA, TOTAL_SCORE, CACHE_FILE
)

logger = logging.getLogger(__name__)


# ========== Pydantic 评分输出模型 ==========

class DimensionScore(BaseModel):
    """单个维度评分"""
    score: int = Field(description="该维度得分（0-满分）")
    comment: str = Field(description="该维度的具体评语")
    suggestions: str = Field(description="该维度的改进建议")


class GradingResult(BaseModel):
    """完整评分结果"""
    total_score: int = Field(description=f"总分（0-{TOTAL_SCORE}）")
    dimensions: dict[str, DimensionScore] = Field(description="各维度评分详情")
    overall_comment: str = Field(description="总体评语")
    overall_suggestions: str = Field(description="总体改进建议")
    strengths: list[str] = Field(description="优点列表")
    weaknesses: list[str] = Field(description="不足列表")


# ========== 请求队列数据结构 ==========

@dataclass
class GradingRequest:
    """评分请求"""
    report_content: str
    style_hint: str
    adjusted_criteria: Optional[dict]
    callback: Callable[[dict, Optional[BaseException]], None]  # 回调函数，接收 (result_dict, error) 两个参数
    priority: int = 0   # 优先级，数字越小优先级越高


# ========== Prompt 模板 ==========

GRADING_PROMPT_TEMPLATE = """你是一位计算机实验课程教师，请对学生实验报告评分。

## 评分标准（总分{total_score}分）

{grading_criteria}

## 要求
1. 逐项打分，评语具体有针对性
2. 建议可操作
3. 优缺点客观平衡

## 输出格式（仅JSON，无其他文字）

{{
    "total_score": <总分>,
    "dimensions": {{
        "实验目的理解": {{"score": <得分>, "comment": "<评语>", "suggestions": "<建议>"}},
        "实验步骤完整性": {{"score": <得分>, "comment": "<评语>", "suggestions": "<建议>"}},
        "结果分析与数据处理": {{"score": <得分>, "comment": "<评语>", "suggestions": "<建议>"}},
        "结论与总结": {{"score": <得分>, "comment": "<评语>", "suggestions": "<建议>"}}
    }},
    "overall_comment": "<总体评语>",
    "overall_suggestions": "<总体改进建议>",
    "strengths": ["<优点1>", "<优点2>"],
    "weaknesses": ["<不足1>", "<不足2>"]
}}

## 实验报告内容

{report_content}

## JSON评分结果：
"""


# ========== 评分缓存管理器 ==========

class GradingCache:
    """评分缓存：相同文本不重复调用API"""

    def __init__(self, cache_file: Path = CACHE_FILE):
        self.cache_file = cache_file
        self.cache = {}
        self._lock = threading.Lock()
        self._load()

    def _load(self):
        """加载缓存"""
        if self.cache_file.exists():
            try:
                with open(self.cache_file, 'r', encoding='utf-8') as f:
                    self.cache = json.load(f)
                logger.info(f"评分缓存已加载，共 {len(self.cache)} 条记录")
            except Exception as e:
                logger.warning(f"加载缓存失败: {e}")
                self.cache = {}
        else:
            self.cache = {}

    def _save(self):
        """保存缓存"""
        try:
            self.cache_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self.cache_file, 'w', encoding='utf-8') as f:
                json.dump(self.cache, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.warning(f"保存缓存失败: {e}")

    def _compute_hash(self, text: str) -> str:
        """计算文本哈希（用于缓存键）"""
        return hashlib.md5(text.encode('utf-8')).hexdigest()

    def get(self, text: str) -> Optional[dict]:
        """获取缓存结果"""
        key = self._compute_hash(text)
        with self._lock:
            if key in self.cache:
                logger.info("命中评分缓存，跳过API调用")
                return self.cache[key]
        return None

    def set(self, text: str, result: dict):
        """设置缓存结果"""
        key = self._compute_hash(text)
        with self._lock:
            self.cache[key] = result
        self._save()
        logger.info("评分结果已缓存")

    def clear(self):
        """清空缓存"""
        with self._lock:
            self.cache = {}
        if self.cache_file.exists():
            self.cache_file.unlink()
        logger.info("评分缓存已清空")

    def get_stats(self) -> dict:
        """获取缓存统计"""
        with self._lock:
            return {
                "total_cached": len(self.cache),
                "cache_file": str(self.cache_file)
            }


# ========== 智能退避速率控制器 ==========

class AdaptiveRateLimiter:
    """
    自适应速率限制器
    - 基础间隔：根据成功/失败动态调整
    - 指数退避：遇到429错误时增加延迟
    - 自适应恢复：成功请求后逐步降低延迟
    """

    def __init__(self,
                 base_delay: float = 2.0,      # 基础间隔（秒）
                 min_delay: float = 0.5,       # 最小间隔
                 max_delay: float = 60.0,      # 最大间隔
                 backoff_factor: float = 2.0,  # 退避倍数
                 recovery_factor: float = 0.9,   # 恢复系数
                 success_threshold: int = 3):   # 连续成功多少次后尝试恢复
        self.base_delay = base_delay
        self.min_delay = min_delay
        self.max_delay = max_delay
        self.backoff_factor = backoff_factor
        self.recovery_factor = recovery_factor
        self.success_threshold = success_threshold

        self._current_delay = base_delay
        self._consecutive_success = 0
        self._consecutive_fail = 0
        self._lock = threading.Lock()
        self._last_request_time = 0

    def wait(self):
        """等待到可以发送下一个请求"""
        with self._lock:
            now = time.time()
            elapsed = now - self._last_request_time
            if elapsed < self._current_delay:
                sleep_time = self._current_delay - elapsed
                logger.info(f"速率限制：等待 {sleep_time:.2f} 秒 (当前间隔: {self._current_delay:.2f}s)")
                time.sleep(sleep_time)
            self._last_request_time = time.time()

    def report_success(self):
        """报告请求成功"""
        with self._lock:
            self._consecutive_success += 1
            self._consecutive_fail = 0

            # 连续成功多次后，尝试降低延迟
            if self._consecutive_success >= self.success_threshold:
                old_delay = self._current_delay
                self._current_delay = max(
                    self.min_delay,
                    self._current_delay * self.recovery_factor
                )
                self._consecutive_success = 0
                if old_delay != self._current_delay:
                    logger.info(f"速率恢复：间隔从 {old_delay:.2f}s 降至 {self._current_delay:.2f}s")

    def report_failure(self, is_rate_limit: bool = False):
        """报告请求失败"""
        with self._lock:
            self._consecutive_success = 0
            self._consecutive_fail += 1

            if is_rate_limit:
                old_delay = self._current_delay
                self._current_delay = min(
                    self.max_delay,
                    self._current_delay * self.backoff_factor
                )
                logger.warning(f"速率退避：间隔从 {old_delay:.2f}s 升至 {self._current_delay:.2f}s "
                              f"(连续失败: {self._consecutive_fail}次)")

    def get_status(self) -> dict:
        """获取当前状态"""
        with self._lock:
            return {
                "current_delay": round(self._current_delay, 2),
                "consecutive_success": self._consecutive_success,
                "consecutive_fail": self._consecutive_fail,
                "min_delay": self.min_delay,
                "max_delay": self.max_delay,
            }


# ========== 串行评分队列处理器 ==========

class SerialGradingQueue:
    """
    串行评分队列
    - 单线程处理，避免并发导致API过载
    - 支持优先级队列
    - 支持取消操作
    """

    def __init__(self, grader: 'LLMGrader'):
        self.grader = grader
        self.queue = Queue()
        self._running = False
        self._worker_thread: Optional[threading.Thread] = None
        self._cancelled = False
        self._processed_count = 0
        self._total_count = 0
        self._lock = threading.Lock()

    def start(self):
        """启动队列处理器"""
        with self._lock:
            if self._running:
                return
            self._running = True
            self._cancelled = False
            self._processed_count = 0

        self._worker_thread = threading.Thread(target=self._worker_loop, daemon=True)
        self._worker_thread.start()
        logger.info("串行评分队列已启动")

    def stop(self):
        """停止队列处理器"""
        with self._lock:
            self._running = False
            self._cancelled = True
        # 清空队列
        while not self.queue.empty():
            try:
                self.queue.get_nowait()
            except Empty:
                break
        if self._worker_thread and self._worker_thread.is_alive():
            self._worker_thread.join(timeout=2)
        logger.info("串行评分队列已停止")

    def enqueue(self, request: GradingRequest):
        """添加请求到队列"""
        self.queue.put(request)
        with self._lock:
            self._total_count += 1
        logger.info(f"评分请求已入队，队列长度: {self.queue.qsize()}")

    def _worker_loop(self):
        """工作线程主循环"""
        while self._running:
            try:
                request = self.queue.get(timeout=1)
            except Empty:
                continue

            if self._cancelled:
                request.callback(None, "操作已取消")
                self.queue.task_done()
                continue

            # 速率限制等待
            self.grader.rate_limiter.wait()

            try:
                result = self.grader._do_grade(
                    request.report_content,
                    request.style_hint,
                    request.adjusted_criteria
                )
                self.grader.rate_limiter.report_success()
                request.callback(result, None)

            except Exception as e:
                error_msg = str(e)
                is_rate_limit = "429" in error_msg or "overloaded" in error_msg.lower()
                self.grader.rate_limiter.report_failure(is_rate_limit)

                if is_rate_limit and not self._cancelled:
                    # 429错误：重新入队，稍后重试
                    logger.warning(f"API过载，请求将重新入队重试: {error_msg}")
                    time.sleep(self.grader.rate_limiter._current_delay)
                    self.queue.put(request)
                else:
                    request.callback(None, error_msg)

            finally:
                self.queue.task_done()
                with self._lock:
                    self._processed_count += 1

    def get_status(self) -> dict:
        """获取队列状态"""
        with self._lock:
            return {
                "running": self._running,
                "queue_size": self.queue.qsize(),
                "processed": self._processed_count,
                "total": self._total_count,
                "rate_limiter": self.grader.rate_limiter.get_status(),
            }

    def is_running(self) -> bool:
        """检查队列是否正在运行"""
        with self._lock:
            return self._running


# ========== LLM评分器 ==========

class LLMGrader:
    """LLM评分器：使用Kimi API进行智能评分（含缓存、队列、退避）"""

    def __init__(self, api_key: Optional[str] = None, temperature: float = 0.3):
        self.api_key = api_key or KIMI_API_KEY
        if not self.api_key:
            raise ValueError("KIMI_API_KEY 未设置！")

        self.llm = ChatOpenAI(
            model=KIMI_MODEL,
            api_key=SecretStr(self.api_key),
            base_url=KIMI_BASE_URL,
            temperature=temperature,
            max_tokens=1500,  # 减少输出token，加快响应
            timeout=120,  # 增加超时到120秒
            max_retries=2  # 最多重试2次
        )

        self.prompt = PromptTemplate(
            template=GRADING_PROMPT_TEMPLATE,
            input_variables=["report_content"],
            partial_variables={
                "grading_criteria": self._format_criteria(),
                "total_score": str(TOTAL_SCORE)
            }
        )

        # 初始化缓存
        self.cache = GradingCache()

        self.rate_limiter = AdaptiveRateLimiter(
            base_delay=2.0,      # 8k 模型可以更快
            min_delay=1.0,
            max_delay=20.0,      # 128k 过载时最多等 20 秒
            backoff_factor=1.5,
            recovery_factor=0.8,
            success_threshold=2
        )

        # 初始化串行队列
        self.queue = SerialGradingQueue(self)

        # 取消事件（用于中断等待）
        self._cancel_event = threading.Event()

        logger.info("LLM评分器初始化完成（含缓存、队列、智能退避）")

    def _log(self, message: str, level: str = "info") -> None:
        """内部日志方法，兼容类内部调用"""
        if level == "warning":
            logger.warning(message)
        elif level == "error":
            logger.error(message)
        elif level == "debug":
            logger.debug(message)
        else:
            logger.info(message)

    def _select_model(self, report_content: str) -> str:
        """根据报告长度动态选择模型"""
        # 估算 token 数（中文字符 ≈ 1 token，英文 ≈ 0.5 token）
        estimated_tokens = len(report_content)
        if estimated_tokens < 6000:
            return "moonshot-v1-8k"
        elif estimated_tokens < 30000:
            return "moonshot-v1-32k"
        else:
            return "moonshot-v1-128k"    

    def _format_criteria(self) -> str:
        """格式化评分标准"""
        lines = []
        for name, info in GRADING_CRITERIA.items():
            lines.append(f"- {name}（满分{info['max_score']}分）：{info['description']}")
        return "\n".join(lines)

    def _build_report_text(self, sections: dict) -> str:
        """构建报告文本"""
        lines = []
        for section_name in ['实验目的', '实验步骤', '实验结果', '实验结论']:
            content = sections.get(section_name, "").strip()
            if content:
                lines.append(f"【{section_name}】")
                lines.append(content)
                lines.append("")

        other = sections.get('其他内容', "").strip()
        if other:
            lines.append("【其他内容】")
            lines.append(other)

        return "\n".join(lines)

    def _do_grade(self, report_content: str, style_hint: str = "",
                  adjusted_criteria: Optional[dict] = None) -> dict:
        """
        实际执行评分（内部方法，由队列调用）
        不处理缓存，缓存检查在调用方处理
        """
        # 构建带风格的Prompt
        criteria_text = self._format_criteria()
        if adjusted_criteria:
            criteria_lines = []
            for name, info in adjusted_criteria.items():
                criteria_lines.append(f"- {name}（满分{info['max_score']}分）：{info['description']}")
            criteria_text = "\n".join(criteria_lines)
            total = sum(c["max_score"] for c in adjusted_criteria.values())
        else:
            total = TOTAL_SCORE

        style_prompt = f"""你是一位计算机实验课程教师，请评分。

## 风格要求
{style_hint}

## 评分标准（总分{total}分）
{criteria_text}

## 要求
1. 逐项打分，评语具体
2. 建议可操作
3. 优缺点客观

## 输出格式（仅JSON）
{{
    "total_score": <总分>,
    "dimensions": {{
        "实验目的理解": {{"score": <得分>, "comment": "<评语>", "suggestions": "<建议>"}},
        "实验步骤完整性": {{"score": <得分>, "comment": "<评语>", "suggestions": "<建议>"}},
        "结果分析与数据处理": {{"score": <得分>, "comment": "<评语>", "suggestions": "<建议>"}},
        "结论与总结": {{"score": <得分>, "comment": "<评语>", "suggestions": "<建议>"}}
    }},
    "overall_comment": "<总体评语>",
    "overall_suggestions": "<改进建议>",
    "strengths": ["<优点1>", "<优点2>"],
    "weaknesses": ["<不足1>", "<不足2>"]
}}

## 实验报告
{report_content}

## JSON结果：
"""

        # 动态选择模型
        model = self._select_model(report_content)
        timeout = 30 if model != "moonshot-v1-128k" else 120
        
        llm = ChatOpenAI(
            model=model,
            api_key=SecretStr(self.api_key),
            base_url=KIMI_BASE_URL,
            temperature=0.3,
            max_tokens=800,
            timeout=timeout,
            max_retries=3
        )
        
        self._log(f"使用模型: {model}（估算 {len(report_content)} 字符）", "info")
        
        response = llm.invoke(style_prompt)
        raw_text = response.content
        if isinstance(raw_text, list):
            raw_text = "".join(
                item.get("content", "") if isinstance(item, dict) else str(item)
                for item in raw_text
            )
        elif isinstance(raw_text, dict):
            raw_text = raw_text.get("content", json.dumps(raw_text, ensure_ascii=False))
        raw_text = str(raw_text).strip()

        # 清理markdown
        if raw_text.startswith("```json"):
            raw_text = raw_text[7:]
        if raw_text.startswith("```"):
            raw_text = raw_text[3:]
        if raw_text.endswith("```"):
            raw_text = raw_text[:-3]
        raw_text = raw_text.strip()

        data = json.loads(raw_text)

        # 构建结果
        dimensions = {}
        criteria_ref = adjusted_criteria or GRADING_CRITERIA
        for name, info in criteria_ref.items():
            max_s = info['max_score'] if isinstance(info, dict) else info
            dim_data = data.get("dimensions", {}).get(name, {})
            dimensions[name] = DimensionScore(
                score=dim_data.get("score", max_s // 2),
                comment=dim_data.get("comment", "无评语"),
                suggestions=dim_data.get("suggestions", "无建议")
            )

        result = GradingResult(
            total_score=data.get("total_score", total // 2),
            dimensions=dimensions,
            overall_comment=data.get("overall_comment", "无总体评语"),
            overall_suggestions=data.get("overall_suggestions", "无建议"),
            strengths=data.get("strengths", ["无"]),
            weaknesses=data.get("weaknesses", ["无"])
        )

        result_dict = self._result_to_dict(result)
        result_dict["total_max"] = total
        result_dict["percentage"] = round(result.total_score / total * 100, 1) if total > 0 else 0

        logger.info(f"评分完成，总分: {result.total_score}")
        return result_dict

    def grade_with_style_to_dict(self, structured_text: dict, style_hint: str = "",
                                  adjusted_criteria: Optional[dict] = None,
                                  use_cache: bool = True) -> dict:
        """
        带风格评分并转字典（同步阻塞方法，用于单文件评分）
        使用队列机制确保串行处理

        Args:
            structured_text: 结构化文本
            style_hint: 风格提示
            adjusted_criteria: 调整后的评分标准
            use_cache: 是否使用缓存（重新批阅时设为False）
        """
        report_content = self._build_report_text(structured_text)
        logger.info(f"开始评分（带风格），报告长度: {len(report_content)} 字符，使用缓存: {use_cache}")

        # 检查缓存
        cache_key = report_content + "|" + style_hint
        if use_cache:
            cached = self.cache.get(cache_key)
            if cached:
                logger.info("命中评分缓存，返回缓存结果")
                return cached

        # 使用队列处理（同步等待）
        result_container = {}
        event = threading.Event()

        def callback(result, error):
            if error:
                result_container["error"] = error
                result_container["result"] = self._default_result_dict(overloaded="429" in error)
            else:
                result_container["result"] = result
            event.set()

        request = GradingRequest(
            report_content=report_content,
            style_hint=style_hint,
            adjusted_criteria=adjusted_criteria,
            callback=callback
        )

        # 确保队列已启动
        if not self.queue._running:
            self.queue.start()

        self.queue.enqueue(request)

        # 可取消的等待：每0.5秒检查一次，避免长时间阻塞无法响应取消
        waited = 0
        timeout = 300  # 最多等待5分钟
        while waited < timeout and not self._cancel_event.is_set():
            if event.wait(timeout=0.5):
                break
            waited += 0.5

        if self._cancel_event.is_set():
            logger.warning("评分请求被取消")
            return self._default_result_dict(overloaded=False)

        if "result" not in result_container:
            return self._default_result_dict(overloaded=True)

        result = result_container["result"]
        # 保存到缓存（即使 use_cache=False，也更新缓存）
        self.cache.set(cache_key, result)
        return result

    def grade_to_dict(self, structured_text: dict, use_cache: bool = True) -> dict:
        """评分并转字典（基础方法）"""
        return self.grade_with_style_to_dict(structured_text, "", None, use_cache=use_cache)

    def _default_result_dict(self, overloaded: bool = False) -> dict:
        """默认结果字典"""
        if overloaded:
            comment = "Kimi API 服务器过载，请稍后重试"
            suggestions = "1. 等待1-2分钟后重新批阅\n2. 减少同时批阅的报告数量\n3. 联系管理员检查API配额"
            overall = "Kimi API 服务器当前过载，评分请求被拒绝。"
        else:
            comment = "评分过程出错，此为默认分数"
            suggestions = "请检查API配置或重试"
            overall = "评分过程出错，请检查API配置或网络连接"

        dimensions = {}
        for name, info in GRADING_CRITERIA.items():
            dimensions[name] = {
                "score": info['max_score'] // 2,
                "max_score": info['max_score'],
                "comment": comment,
                "suggestions": suggestions
            }

        return {
            "total_score": TOTAL_SCORE // 2,
            "total_max": TOTAL_SCORE,
            "percentage": 50.0,
            "dimensions": dimensions,
            "overall_comment": overall,
            "overall_suggestions": suggestions,
            "strengths": ["无"],
            "weaknesses": ["评分失败" if not overloaded else "API服务器过载"]
        }

    def _result_to_dict(self, result: GradingResult) -> dict:
        """转字典"""
        return {
            "total_score": result.total_score,
            "total_max": TOTAL_SCORE,
            "percentage": round(result.total_score / TOTAL_SCORE * 100, 1),
            "dimensions": {
                name: {
                    "score": d.score,
                    "max_score": GRADING_CRITERIA[name]['max_score'],
                    "comment": d.comment,
                    "suggestions": d.suggestions
                }
                for name, d in result.dimensions.items()
            },
            "overall_comment": result.overall_comment,
            "overall_suggestions": result.overall_suggestions,
            "strengths": result.strengths,
            "weaknesses": result.weaknesses
        }

    def get_cache_stats(self) -> dict:
        """获取缓存统计"""
        return self.cache.get_stats()

    def clear_cache(self):
        """清空缓存"""
        self.cache.clear()

    def get_queue_status(self) -> dict:
        """获取队列状态"""
        return self.queue.get_status()

    def get_rate_limiter_status(self) -> dict:
        """获取速率限制器状态"""
        return self.rate_limiter.get_status()

    def start_queue(self):
        """启动评分队列"""
        self.queue.start()

    def stop_queue(self):
        """停止评分队列"""
        self._cancel_event.set()  # 设置取消标志
        self.queue.stop()

    def reset_cancel(self):
        """重置取消标志"""
        self._cancel_event.clear()

    def is_queue_running(self) -> bool:
        """检查队列是否正在运行"""
        return self.queue.is_running()