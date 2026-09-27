"""
OCR模块测试脚本
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from modules.ocr_processor import OCRProcessor
from modules.text_structurer import TextStructurer
from config import INPUT_DIR


def test_single_image():
    """测试单张图片识别"""
    print("=" * 50)
    print("测试1：单张图片OCR识别")
    print("=" * 50)
    
    ocr = OCRProcessor()
    
    # 创建一个测试图片（如果没有真实报告，先用代码生成）
    test_img = INPUT_DIR / "test_report.png"
    
    if not test_img.exists():
        print("⚠️ 未找到测试图片，正在生成示例图片...")
        from PIL import Image, ImageDraw, ImageFont
        
        # 创建一张模拟实验报告的图片
        img = Image.new('RGB', (800, 1000), color='white')
        draw = ImageDraw.Draw(img)
        
        # 尝试使用系统字体
        try:
            font = ImageFont.truetype("msyh.ttc", 24)  # 微软雅黑
        except (OSError, IOError):
            font = ImageFont.load_default()
        
        # 写入模拟实验报告内容
        content = [
            "实验报告",
            "",
            "实验目的：",
            "1. 掌握Python基本语法",
            "2. 学习文件操作方法",
            "",
            "实验步骤：",
            "1. 安装Python环境",
            "2. 编写Hello World程序",
            "3. 运行并调试代码",
            "",
            "实验结果：",
            "程序成功运行，输出Hello World",
            "",
            "实验结论：",
            "通过本次实验，掌握了Python基础编程技能。"
        ]
        
        y = 20
        for line in content:
            draw.text((20, y), line, fill='black', font=font)
            y += 35
        
        img.save(test_img)
        print(f"✅ 示例图片已生成: {test_img}")
    
    # 执行OCR
    print(f"\n正在识别: {test_img}")
    text = ocr.recognize(test_img, preprocess=True)
    
    print(f"\n识别结果（前200字符）:\n{text[:200]}...")
    print(f"\n总字符数: {len(text)}")
    
    return text


def test_text_structure(text: str):
    """测试文本结构化"""
    print("\n" + "=" * 50)
    print("测试2：文本结构化")
    print("=" * 50)
    
    structurer = TextStructurer()
    
    # 清洗
    cleaned = structurer.clean_text(text)
    print(f"清洗后文本长度: {len(cleaned)}")
    
    # 分段
    sections = structurer.extract_sections(cleaned)
    
    print("\n各段落提取结果:")
    for name, content in sections.items():
        preview = content[:80].replace('\n', ' ') if content else "[空]"
        print(f"  [{name}]: {preview}...")
    
    # 摘要
    summary = structurer.get_text_summary(cleaned)
    print("\n文本摘要:")
    print(f"  总字符数: {summary['total_chars']}")
    print(f"  总行数: {summary['total_lines']}")
    print(f"  检测到的段落: {summary['has_sections']}")
    print(f"  段落占比: {summary['section_ratios']}")
    
    return sections


def test_batch():
    """测试批量处理"""
    print("\n" + "=" * 50)
    print("测试3：批量处理")
    print("=" * 50)
    
    ocr = OCRProcessor()
    results = ocr.batch_process(INPUT_DIR)
    
    print(f"\n共处理 {len(results)} 个文件:")
    for name, text in results.items():
        status = "✅" if not text.startswith("[ERROR]") else "❌"
        print(f"  {status} {name}: {len(text)} 字符")
    
    return results


if __name__ == "__main__":
    print("实验报告批阅智能体 - OCR模块测试")
    print("=" * 50)
    
    # 测试1：单图识别
    text = test_single_image()
    
    # 测试2：文本结构化
    if text:
        sections = test_text_structure(text)
    
    # 测试3：批量处理
    batch_results = test_batch()
    
    print("\n" + "=" * 50)
    print("所有测试完成！")
    print("=" * 50)