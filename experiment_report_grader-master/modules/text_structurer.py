"""
文本结构化模块
负责：OCR结果清洗、段落分段、关键信息提取
"""

import re
import logging

logger = logging.getLogger(__name__)


class TextStructurer:
    """文本结构化处理器"""

    def __init__(self):
        self.section_keywords = {
            '实验目的': ['实验目的', '目的', '实验目标', '目标'],
            '实验步骤': ['实验步骤', '步骤', '实验过程', '操作流程', '操作步骤'],
            '实验结果': ['实验结果', '结果', '数据分析', '实验数据', '数据记录'],
            '实验结论': ['实验结论', '结论', '总结', '实验总结', '心得体会']
        }

    def clean_text(self, raw_text: str) -> str:
        """清洗OCR识别结果"""
        if not raw_text:
            return ""

        text = re.sub(r'[^\u4e00-\u9fa5a-zA-Z0-9\s.,;:!?，。；：！？、（）()\[\]{}""''/\\-]', '', raw_text)
        text = re.sub(r'(\w)-\n(\w)', r'\1\2', text)
        text = re.sub(r'[ \t]+', ' ', text)
        text = re.sub(r'\n{3,}', '\n\n', text)

        lines = [line.strip() for line in text.split('\n')]
        text = '\n'.join(line for line in lines if line)

        logger.info(f"文本清洗完成，原始长度: {len(raw_text)} -> 清洗后: {len(text)}")
        return text

    def extract_sections(self, text: str) -> dict:
        """提取各段落：实验目的、步骤、结果、结论"""
        sections = {key: "" for key in self.section_keywords.keys()}
        sections['其他内容'] = ""

        lines = text.split('\n')
        current_section = '其他内容'

        for line in lines:
            line = line.strip()
            if not line:
                continue

            matched = False
            for section_name, keywords in self.section_keywords.items():
                line_start = line[:15]
                if any(kw in line_start for kw in keywords):
                    current_section = section_name
                    content = line
                    for kw in keywords:
                        content = content.replace(kw, '', 1)
                    content = content.strip(' ：:')
                    if content:
                        sections[current_section] += content + '\n'
                    matched = True
                    break

            if not matched:
                sections[current_section] += line + '\n'

        # 如果"实验结论"为空，尝试从"实验结果"末尾提取
        if not sections['实验结论'] and sections['实验结果']:
            result_text = sections['实验结果']
            conclusion_markers = ['通过本次', '综上所述', '总之', '结论', '总结', '本次实验']
            for marker in conclusion_markers:
                pos = result_text.find(marker)
                if pos != -1 and pos > len(result_text) * 0.3:
                    sections['实验结论'] = result_text[pos:].strip()
                    sections['实验结果'] = result_text[:pos].strip()
                    break

        for key in sections:
            sections[key] = sections[key].strip()

        logger.info(f"段落提取完成: {[(k, len(v)) for k, v in sections.items()]}")
        return sections

    def get_text_summary(self, text: str) -> dict:
        """获取文本摘要信息"""
        sections = self.extract_sections(text)
        total_chars = len(text)
        has_sections = [k for k, v in sections.items() if v and k != '其他内容']

        section_ratios = {}
        for name, content in sections.items():
            if content:
                section_ratios[name] = round(len(content) / total_chars * 100, 1)

        return {
            'total_chars': total_chars,
            'total_lines': len(text.split('\n')),
            'has_sections': has_sections,
            'section_ratios': section_ratios
        }