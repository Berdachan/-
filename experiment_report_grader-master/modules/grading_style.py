"""
批阅风格管理模块
负责：预设批阅风格、自定义风格、动态调整评分权重和维度
"""

import json
import logging
from typing import Optional
from copy import deepcopy

from config import GRADING_CRITERIA, TOTAL_SCORE, CACHE_DIR

logger = logging.getLogger(__name__)


# ========== 预设批阅风格 ==========

PRESET_STYLES = {
    "standard": {
        "name": "标准风格",
        "description": "公正客观，严格按照评分标准打分，评语中规中矩",
        "tone": "客观、严谨",
        "strictness": 1.0,
        "weight_adjustments": {},
        "comment_style": "标准",
        "emphasis": [],
    },
    "strict": {
        "name": "严格风格",
        "description": "高标准严要求，扣分较狠，评语直接指出问题",
        "tone": "严厉、直接",
        "strictness": 1.3,
        "weight_adjustments": {
            "实验步骤完整性": 1.2,
            "结果分析与数据处理": 1.2,
        },
        "comment_style": "严厉",
        "emphasis": ["规范性", "准确性", "严谨性"],
    },
    "gentle": {
        "name": "温和风格",
        "description": "以鼓励为主，适当扣分，评语侧重优点和改进方向",
        "tone": "温和、鼓励",
        "strictness": 0.8,
        "weight_adjustments": {
            "实验目的理解": 1.1,
            "结论与总结": 1.1,
        },
        "comment_style": "鼓励",
        "emphasis": ["创新性", "思考深度", "表达能力"],
    },
    "research": {
        "name": "科研风格",
        "description": "注重科研思维和数据分析，对数据处理要求高",
        "tone": "专业、学术",
        "strictness": 1.1,
        "weight_adjustments": {
            "结果分析与数据处理": 1.3,
            "实验步骤完整性": 0.9,
        },
        "comment_style": "学术",
        "emphasis": ["数据分析", "逻辑推理", "实验设计"],
    },
    "engineering": {
        "name": "工程风格",
        "description": "注重工程实践和代码规范，对操作步骤要求高",
        "tone": "务实、工程化",
        "strictness": 1.0,
        "weight_adjustments": {
            "实验步骤完整性": 1.3,
            "实验目的理解": 0.9,
        },
        "comment_style": "工程",
        "emphasis": ["代码规范", "工程实践", "可复现性"],
    },
}


class GradingStyleManager:
    """批阅风格管理器"""

    def __init__(self):
        self.custom_styles_file = CACHE_DIR / "custom_styles.json"
        self.custom_styles = {}
        self.current_style = "standard"
        self._load_custom_styles()

    def _load_custom_styles(self):
        if self.custom_styles_file.exists():
            try:
                with open(self.custom_styles_file, 'r', encoding='utf-8') as f:
                    self.custom_styles = json.load(f)
                logger.info(f"已加载 {len(self.custom_styles)} 个自定义风格")
            except Exception as e:
                logger.warning(f"加载自定义风格失败: {e}")
                self.custom_styles = {}
        else:
            self.custom_styles = {}

    def _save_custom_styles(self):
        try:
            self.custom_styles_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self.custom_styles_file, 'w', encoding='utf-8') as f:
                json.dump(self.custom_styles, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.warning(f"保存自定义风格失败: {e}")

    def get_all_styles(self) -> dict:
        all_styles = deepcopy(PRESET_STYLES)
        all_styles.update(self.custom_styles)
        return all_styles

    def get_style_names(self) -> list:
        return list(self.get_all_styles().keys())

    def get_style_info(self, style_key: Optional[str] = None) -> dict:
        key = style_key or self.current_style
        styles = self.get_all_styles()
        return styles.get(key, PRESET_STYLES["standard"])

    def set_current_style(self, style_key: str) -> bool:
        if style_key in self.get_all_styles():
            self.current_style = style_key
            logger.info(f"已切换批阅风格: {self.get_style_info(style_key)['name']}")
            return True
        return False

    def get_current_style(self) -> str:
        return self.current_style

    def get_adjusted_criteria(self, style_key: Optional[str] = None) -> dict:
        style = self.get_style_info(style_key)
        base_criteria = deepcopy(GRADING_CRITERIA)
        adjusted = {}
        total = 0

        for name, info in base_criteria.items():
            base_max = info["max_score"]
            strictness = style.get("strictness", 1.0)
            weight_adj = style.get("weight_adjustments", {}).get(name, 1.0)
            adjusted_max = round(base_max * weight_adj * strictness)
            adjusted_max = max(5, min(adjusted_max, base_max + 10))

            adjusted[name] = {
                "description": info["description"],
                "max_score": adjusted_max,
                "original_max": base_max,
                "weight_factor": weight_adj,
            }
            total += adjusted_max

        if total != TOTAL_SCORE:
            factor = TOTAL_SCORE / total
            for name in adjusted:
                adjusted[name]["max_score"] = round(adjusted[name]["max_score"] * factor)
            current_total = sum(a["max_score"] for a in adjusted.values())
            diff = TOTAL_SCORE - current_total
            if diff != 0:
                max_dim = max(adjusted.keys(), key=lambda k: adjusted[k]["max_score"])
                adjusted[max_dim]["max_score"] += diff

        return adjusted

    def create_custom_style(self, key: str, name: str, description: str,
                           strictness: float = 1.0,
                           weight_adjustments: Optional[dict] = None,
                           tone: str = "客观",
                           comment_style: str = "标准",
                           emphasis: Optional[list] = None) -> bool:
        if key in PRESET_STYLES:
            logger.warning(f"'{key}' 是预设风格，不能覆盖")
            return False

        self.custom_styles[key] = {
            "name": name,
            "description": description,
            "tone": tone,
            "strictness": strictness,
            "weight_adjustments": weight_adjustments or {},
            "comment_style": comment_style,
            "emphasis": emphasis or [],
        }
        self._save_custom_styles()
        logger.info(f"已创建自定义风格: {name}")
        return True

    def delete_custom_style(self, key: str) -> bool:
        if key in self.custom_styles:
            del self.custom_styles[key]
            self._save_custom_styles()
            if self.current_style == key:
                self.current_style = "standard"
            return True
        return False

    def get_style_prompt_hint(self, style_key: Optional[str] = None) -> str:
        style = self.get_style_info(style_key)
        tone = style.get("tone", "客观、严谨")
        comment_style = style.get("comment_style", "标准")
        emphasis = style.get("emphasis", [])
        strictness = style.get("strictness", 1.0)

        hints = []
        hints.append(f"请以{tone}的语气进行评分和评语撰写。")

        if strictness > 1.1:
            hints.append("评分标准严格，对错误和不足之处要明确指出并适当扣分。")
        elif strictness < 0.9:
            hints.append("评分标准相对宽松，以鼓励和引导为主，适当包容小错误。")

        if comment_style == "严厉":
            hints.append("评语要直接指出问题，不回避缺点，但同时给出具体改进建议。")
        elif comment_style == "鼓励":
            hints.append("评语要先肯定优点和亮点，再温和地指出可以改进的地方。")
        elif comment_style == "学术":
            hints.append("评语要使用学术语言，关注实验设计的科学性、数据分析的严谨性。")
        elif comment_style == "工程":
            hints.append("评语要关注工程实践价值，强调代码规范、可复现性和实用性。")

        if emphasis:
            hints.append(f"特别关注以下方面：{', '.join(emphasis)}。")

        return "\n".join(hints)

    def get_style_display_list(self) -> list[tuple]:
        styles = self.get_all_styles()
        result = []
        for key in PRESET_STYLES:
            result.append((key, f"【预设】{styles[key]['name']}"))
        for key in self.custom_styles:
            result.append((key, f"【自定义】{styles[key]['name']}"))
        return result