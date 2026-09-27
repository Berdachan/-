"""
报告质量检测模块
负责：OCR置信度评估、低质量报告提醒、文本质量分析
"""

import logging
from pathlib import Path
from typing import Optional
from dataclasses import dataclass

import numpy as np
from PIL import Image

logger = logging.getLogger(__name__)


@dataclass
class QualityReport:
    filename: str
    overall_score: float
    is_acceptable: bool
    ocr_confidence: float
    text_quality_score: float
    structure_score: float
    warnings: list[str]
    suggestions: list[str]
    details: dict


class ReportQualityChecker:
    """报告质量检测器"""

    def __init__(self, ocr_processor=None):
        self.ocr = ocr_processor
        self.min_confidence = 60
        self.min_text_length = 100
        self.min_sections = 2
        self.cv2 = self._load_cv2()

    def _load_cv2(self):
        try:
            import importlib
            return importlib.import_module("cv2")
        except ImportError:
            return None

    def check_image_quality(self, image_path: Path) -> dict:
        img = Image.open(image_path)
        width, height = img.size

        resolution_score = min(100, (width * height) / (1000 * 1000) * 100)

        if self.cv2 is not None:
            img_cv = self.cv2.imread(str(image_path), self.cv2.IMREAD_GRAYSCALE)
            if img_cv is not None:
                laplacian_var = self.cv2.Laplacian(img_cv, self.cv2.CV_64F).var()
                blur_score = min(100, laplacian_var / 10)
                contrast = img_cv.std()
                contrast_score = min(100, contrast / 2)
            else:
                blur_score = 50
                contrast_score = 50
        else:
            blur_score = 50
            contrast_score = 50

        return {
            "resolution_score": round(resolution_score, 1),
            "blur_score": round(blur_score, 1),
            "contrast_score": round(contrast_score, 1),
            "width": width,
            "height": height,
        }

    def check_ocr_confidence(self, image_path: Path) -> dict:
        if self.ocr is None:
            return {"confidence": 0, "word_count": 0, "low_confidence_words": []}

        try:
            results = self.ocr.recognize_with_confidence(image_path, preprocess=True)

            if not results:
                return {"confidence": 0, "word_count": 0, "low_confidence_words": []}

            confidences = [r["conf"] for r in results]
            avg_conf = np.mean(confidences) if confidences else 0

            low_conf_words = [
                r["text"] for r in results
                if r["conf"] < self.min_confidence and len(r["text"]) > 1
            ]

            return {
                "confidence": round(avg_conf, 1),
                "word_count": len(results),
                "low_confidence_words": low_conf_words[:20],
                "min_confidence": round(min(confidences), 1) if confidences else 0,
                "max_confidence": round(max(confidences), 1) if confidences else 0,
            }
        except Exception as e:
            logger.error(f"OCR置信度检测失败: {e}")
            return {"confidence": 0, "word_count": 0, "low_confidence_words": [], "error": str(e)}

    def check_text_quality(self, text: str) -> dict:
        if not text:
            return {"score": 0, "length": 0, "garbage_ratio": 1.0, "has_structure": False}

        length = len(text)
        import re
        total_chars = len(text)
        valid_chars = len(re.findall(r'[\u4e00-\u9fa5a-zA-Z0-9\s.,;:!?，。；：！？、（）()\[\]{}""''/\\-]', text))
        garbage_ratio = 1 - (valid_chars / total_chars) if total_chars > 0 else 1

        has_structure = bool(re.search(r'(实验目的|实验步骤|实验结果|结论|一、|二、|1\.|2\.)', text))

        experiment_keywords = ['实验', '数据', '结果', '分析', '步骤', '结论', '目的']
        keyword_count = sum(1 for kw in experiment_keywords if kw in text)

        score = 100
        if length < self.min_text_length:
            score -= 30
        if garbage_ratio > 0.3:
            score -= 40
        if not has_structure:
            score -= 20
        if keyword_count < 3:
            score -= 15

        score = max(0, score)

        return {
            "score": round(score, 1),
            "length": length,
            "garbage_ratio": round(garbage_ratio, 3),
            "has_structure": has_structure,
            "keyword_count": keyword_count,
        }

    def check_structure(self, sections: dict) -> dict:
        expected_sections = ['实验目的', '实验步骤', '实验结果', '实验结论']
        found_sections = [s for s in expected_sections if sections.get(s, '').strip()]

        section_ratio = len(found_sections) / len(expected_sections)

        section_lengths = {}
        for sec in expected_sections:
            content = sections.get(sec, '')
            section_lengths[sec] = len(content)

        score = section_ratio * 100

        for sec, length in section_lengths.items():
            if length > 0 and length < 20:
                score -= 10

        score = max(0, score)

        return {
            "score": round(score, 1),
            "found_sections": found_sections,
            "missing_sections": [s for s in expected_sections if s not in found_sections],
            "section_lengths": section_lengths,
            "section_ratio": round(section_ratio, 2),
        }

    def full_check(self, filename: str, image_path: Optional[Path] = None,
                   text: Optional[str] = None, sections: Optional[dict] = None) -> QualityReport:
        warnings = []
        suggestions = []
        details = {}

        if image_path and image_path.exists():
            img_quality = self.check_image_quality(image_path)
            details["image_quality"] = img_quality

            if img_quality["resolution_score"] < 50:
                warnings.append(f"图片分辨率较低 ({img_quality['width']}x{img_quality['height']})")
                suggestions.append("建议使用更高分辨率的扫描件或拍照")

            if img_quality["blur_score"] < 40:
                warnings.append("图片可能存在模糊")
                suggestions.append("请确保图片清晰，避免手抖或对焦不准")

            if img_quality["contrast_score"] < 40:
                warnings.append("图片对比度较低")
                suggestions.append("建议调整扫描参数或改善拍摄光线")

        text_quality = self.check_text_quality(text) if text else {"score": 0}

        if image_path and image_path.exists() and self.ocr:
            ocr_result = self.check_ocr_confidence(image_path)
            details["ocr_confidence"] = ocr_result
            ocr_conf = ocr_result.get("confidence", 0)

            if ocr_conf < self.min_confidence:
                warnings.append(f"OCR识别置信度较低 ({ocr_conf}%)")
                suggestions.append("建议重新扫描或拍照，确保文字清晰可辨")

            if ocr_result.get("low_confidence_words"):
                low_words = ocr_result["low_confidence_words"][:5]
                warnings.append(f"存在 {len(ocr_result['low_confidence_words'])} 个低置信度识别词")
                suggestions.append(f"疑似识别错误: {', '.join(low_words)}...")
        else:
            # 非图片格式（PDF/DOCX），无OCR过程，用文本质量分替代
            ocr_conf = text_quality.get("score", 80) if text else 80

        if text:
            details["text_quality"] = text_quality

            if text_quality["score"] < 60:
                warnings.append(f"文本质量较低 (得分: {text_quality['score']})")
                if text_quality["garbage_ratio"] > 0.3:
                    suggestions.append("文本中存在大量乱码，请检查原文件质量")
                if text_quality["length"] < self.min_text_length:
                    suggestions.append(f"文本内容过短 ({text_quality['length']} 字符)，可能识别不完整")
        else:
            text_quality = {"score": 0}

        if sections:
            structure = self.check_structure(sections)
            details["structure"] = structure

            if structure["score"] < 60:
                warnings.append(f"报告结构不完整 (得分: {structure['score']})")
                missing = structure.get("missing_sections", [])
                if missing:
                    suggestions.append(f"缺少以下段落: {', '.join(missing)}")
        else:
            structure = {"score": 0}

        weights = {"ocr": 0.3, "text": 0.3, "structure": 0.4}

        overall = (
            ocr_conf * weights["ocr"] +
            text_quality["score"] * weights["text"] +
            structure["score"] * weights["structure"]
        )

        is_acceptable = overall >= 50 and len(warnings) < 3

        return QualityReport(
            filename=filename,
            overall_score=round(overall, 1),
            is_acceptable=is_acceptable,
            ocr_confidence=round(ocr_conf, 1),
            text_quality_score=text_quality["score"],
            structure_score=structure["score"],
            warnings=warnings,
            suggestions=suggestions,
            details=details,
        )