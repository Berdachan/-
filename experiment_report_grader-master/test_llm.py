"""
LLM评分模块测试脚本
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from modules.llm_grader import LLMGrader


def test_llm_grader():
    """测试LLM评分功能"""
    print("=" * 60)
    print("测试：LLM评分模块")
    print("=" * 60)
    
    # 检查API密钥
    from config import KIMI_API_KEY
    if not KIMI_API_KEY:
        print("❌ KIMI_API_KEY 未设置！")
        print("请设置环境变量：set KIMI_API_KEY=你的密钥")
        print("或在 config.py 中直接填写")
        return
    
    print(f"✅ API密钥已配置（前8位: {KIMI_API_KEY[:8]}...）")
    print("模型: moonshot-v1-128k")
    print("总分: 100分")
    print("-" * 60)
    
    # 准备测试文本（模拟OCR+结构化后的结果）
    test_sections = {
        '实验目的': '1. 掌握Python基本语法\n2. 学习文件操作方法',
        '实验步骤': '1. 安装Python环境\n2. 编写Hello World程序\n3. 运行并调试代码\n4. 学习变量和数据类型',
        '实验结果': '程序成功运行，输出Hello World\n学会了print函数的使用',
        '实验结论': '通过本次实验，掌握了Python基础编程技能。对编程有了初步认识。',
        '其他内容': ''
    }
    
    print("\n待评分报告内容:")
    for name, content in test_sections.items():
        if content:
            preview = content[:50].replace('\n', ' ')
            print(f"  [{name}]: {preview}...")
    
    print("\n" + "=" * 60)
    print("正在调用Kimi API进行评分（请等待10-30秒）...")
    print("=" * 60)
    
    try:
        # 初始化评分器
        grader = LLMGrader(temperature=0.3)
        
        # 执行评分
        result = grader.grade_to_dict(test_sections)
        
        # 显示结果
        print("\n" + "=" * 60)
        print("评分结果")
        print("=" * 60)
        
        print(f"\n📊 总分: {result['total_score']} / {result['total_max']} ({result['percentage']}%)")
        
        print("\n📋 各维度评分:")
        for name, detail in result['dimensions'].items():
            print(f"\n  【{name}】")
            print(f"    得分: {detail['score']} / {detail['max_score']}")
            print(f"    评语: {detail['comment'][:60]}...")
            print(f"    建议: {detail['suggestions'][:60]}...")
        
        print("\n💬 总体评语:")
        print(f"  {result['overall_comment']}")
        
        print("\n💡 总体建议:")
        print(f"  {result['overall_suggestions']}")
        
        print("\n✅ 优点:")
        for s in result['strengths']:
            print(f"  - {s}")
        
        print("\n⚠️ 不足:")
        for w in result['weaknesses']:
            print(f"  - {w}")
        
        print("\n" + "=" * 60)
        print("✅ 评分测试完成！")
        print("=" * 60)
        
        return result
        
    except Exception as e:
        print(f"\n❌ 评分失败: {e}")
        import traceback
        traceback.print_exc()
        return None


if __name__ == "__main__":
    test_llm_grader()