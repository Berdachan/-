"""
实验报告批阅智能体 - CLI 主程序（v4.0）
支持：多用户、MySQL持久化、历史记录、命令行交互
"""

import sys
from pathlib import Path

# 添加项目路径（必须在其他导入之前）
sys.path.insert(0, str(Path(__file__).parent))

import logging
import threading
import time
from typing import Optional, List

from config import INPUT_DIR, OUTPUT_DIR, SUPPORTED_IMAGE_FORMATS, ALL_SUPPORTED_FORMATS
from modules.database import DatabaseManager  # type: ignore
from modules.auth import AuthManager  # type: ignore
from modules.history_manager import HistoryManager  # type: ignore
from modules.ocr_processor import OCRProcessor
from modules.text_structurer import TextStructurer
from modules.llm_grader import LLMGrader
from modules.exporter import ResultExporter
from modules.grading_style import GradingStyleManager
from modules.quality_checker import ReportQualityChecker

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler('grading_cli.log', encoding='utf-8')
    ]
)
logger = logging.getLogger(__name__)


class GradingCLI:
    """命令行批阅系统"""

    def __init__(self):
        self.db = DatabaseManager()
        self.auth = AuthManager(self.db)
        self.history = HistoryManager(self.db)
        self.ocr = OCRProcessor()
        self.structurer = TextStructurer()
        self.exporter = ResultExporter()
        self.style_manager = GradingStyleManager()
        self.quality_checker = ReportQualityChecker(self.ocr)

        self.reports: dict = {}  # 当前会话的报告
        self._grading_cancelled = False
        self._grading_thread: Optional[threading.Thread] = None

        # 尝试初始化评分器
        try:
            self.grader = LLMGrader()
            self.grader_available = True
        except ValueError:
            self.grader = None
            self.grader_available = False

    # ========== 用户认证 ==========

    def show_auth_menu(self):
        """显示认证菜单"""
        while True:
            print("\n" + "=" * 50)
            print("  实验报告批阅智能体 v4.0")
            print("=" * 50)
            print("1. 登录")
            print("2. 注册")
            print("3. 自动登录")
            print("0. 退出")
            print("-" * 50)

            choice = input("请选择: ").strip()

            if choice == "1":
                self._do_login()
            elif choice == "2":
                self._do_register()
            elif choice == "3":
                self._do_auto_login()
            elif choice == "0":
                print("再见！")
                sys.exit(0)
            else:
                print("无效选择")

    def _do_login(self):
        """登录流程"""
        username = input("用户名: ").strip()
        password = input("密码: ").strip()
        auto = input("是否自动登录? (y/n): ").strip().lower() == "y"

        success, msg = self.auth.login(username, password, auto_login=auto)
        if success:
            print(f"✅ {msg}")
            self.show_main_menu()
        else:
            print(f"❌ {msg}")

    def _do_register(self):
        """注册流程"""
        username = input("用户名: ").strip()
        password = input("密码: ").strip()
        confirm = input("确认密码: ").strip()

        if password != confirm:
            print("❌ 两次密码不一致")
            return

        success, msg = self.auth.register(username, password)
        print(f"{'✅' if success else '❌'} {msg}")

    def _do_auto_login(self):
        """自动登录"""
        user = self.auth.check_auto_login()
        if user:
            print(f"✅ 自动登录成功: {user.username}")
            self.show_main_menu()
        else:
            print("❌ 没有可用的自动登录凭证")

    # ========== 主菜单 ==========

    def show_main_menu(self):
        """显示主菜单"""
        user = self.auth.get_current_user()
        if not user:
            print("❌ 未登录")
            return

        while True:
            print("\n" + "=" * 50)
            print(f"  欢迎, {user.username} (ID: {user.id})")
            print("=" * 50)
            print("1. 导入报告")
            print("2. 查看报告列表")
            print("3. 开始批阅")
            print("4. 停止批阅")
            print("5. 查看批阅结果")
            print("6. 导出Excel")
            print("7. 导出PDF")
            print("8. 历史记录")
            print("9. 导出历史记录")
            print("10. 重新批阅历史报告")
            print("11. 切换批阅风格")
            print("12. 质量检测")
            print("13. 数据仪表盘")
            print("14. 个人统计")
            print("0. 退出登录")
            print("-" * 50)

            choice = input("请选择: ").strip()

            if choice == "1":
                self._import_reports()
            elif choice == "2":
                self._list_reports()
            elif choice == "3":
                self._start_grading()
            elif choice == "4":
                self._stop_grading()
            elif choice == "5":
                self._show_results()
            elif choice == "6":
                self._export_excel()
            elif choice == "7":
                self._export_pdf()
            elif choice == "8":
                self._show_history()
            elif choice == "9":
                self._export_history()
            elif choice == "10":
                self._regrade_history()
            elif choice == "11":
                self._switch_style()
            elif choice == "12":
                self._check_quality()
            elif choice == "13":
                self._show_dashboard()
            elif choice == "14":
                self._show_statistics()
            elif choice == "0":
                self.auth.logout()
                print("已退出登录")
                break
            else:
                print("无效选择")

    # ========== 报告管理 ==========

    def _import_reports(self):
        """导入报告"""
        path = input("请输入文件或文件夹路径: ").strip()
        path_obj = Path(path)

        if not path_obj.exists():
            print("❌ 路径不存在")
            return

        count = 0
        if path_obj.is_file():
            if self._import_single(path_obj):
                count += 1
        elif path_obj.is_dir():
            for f in path_obj.rglob("*"):
                if f.is_file() and f.suffix.lower() in ALL_SUPPORTED_FORMATS:
                    if self._import_single(f):
                        count += 1

        print(f"✅ 成功导入 {count} 个报告")

    def _import_single(self, path: Path) -> bool:
        """导入单个文件"""
        filename = path.name
        if filename in self.reports:
            return False

        dest = INPUT_DIR / filename
        import shutil
        shutil.copy(path, dest)

        self.reports[filename] = {
            "path": dest,
            "text": "",
            "sections": {},
            "score": None,
            "status": "待处理",
            "quality": None,
        }
        return True

    def _list_reports(self):
        """查看报告列表"""
        if not self.reports:
            print("暂无报告")
            return

        print("\n" + "-" * 60)
        print(f"{'序号':<6}{'文件名':<30}{'状态':<10}{'分数':<10}{'质量':<10}")
        print("-" * 60)
        for i, (name, data) in enumerate(self.reports.items(), 1):
            status = data["status"]
            score = f"{data['score']['total_score']}/{data['score']['total_max']}" if data.get("score") else "-"
            quality = f"{data['quality'].overall_score}" if data.get("quality") else "-"
            print(f"{i:<6}{name:<30}{status:<10}{score:<10}{quality:<10}")
        print("-" * 60)

    # ========== 批阅 ==========

    def _start_grading(self):
        """开始批阅"""
        if not self.grader_available:
            print("❌ API未配置")
            return

        pending = [f for f, d in self.reports.items() if d["status"] == "待处理"]
        if not pending:
            print("没有待处理的报告")
            return

        self._grading_cancelled = False
        if self.grader:
            self.grader.reset_cancel()

        style_info = self.style_manager.get_style_info()
        print(f"使用风格: {style_info['name']}")

        self._grading_thread = threading.Thread(target=self._grading_worker, args=(pending,))
        self._grading_thread.daemon = True
        self._grading_thread.start()

        print(f"✅ 开始批阅 {len(pending)} 个报告...")
        print("输入 'stop' 可停止批阅")

        # 等待批阅完成或用户输入stop
        while self._grading_thread.is_alive():
            try:
                user_input = input().strip().lower()
                if user_input == "stop":
                    self._stop_grading()
                    break
            except EOFError:
                time.sleep(1)

    def _grading_worker(self, filenames: List[str]):
        """批阅工作线程"""
        user = self.auth.get_current_user()
        if not user:
            return

        for i, filename in enumerate(filenames):
            if self._grading_cancelled:
                print("\n⚠️ 批阅已取消")
                break

            print(f"\n[{i+1}/{len(filenames)}] 处理: {filename}")
            report = self.reports[filename]

            try:
                ext = report["path"].suffix.lower()

                # OCR识别
                if ext == ".pdf":
                    pages = self.ocr.process_pdf(report["path"])
                    text = "\n\n".join(pages)
                elif ext == ".docx":
                    text = self.ocr.process_docx(report["path"])
                elif ext == ".doc":
                    text = self.ocr.process_doc(report["path"])
                elif ext in SUPPORTED_IMAGE_FORMATS:
                    text = self.ocr.recognize(report["path"])
                else:
                    raise ValueError(f"不支持的格式: {ext}")

                report["text"] = text
                print(f"  ✅ OCR完成 ({len(text)} 字符)")

                # 结构化
                cleaned = self.structurer.clean_text(text)
                sections = self.structurer.extract_sections(cleaned)
                report["sections"] = sections
                print("  ✅ 结构化完成")

                # 质量检测
                image_path = report["path"] if ext in SUPPORTED_IMAGE_FORMATS else None
                quality = self.quality_checker.full_check(filename, image_path, text, sections)
                report["quality"] = quality
                print(f"  ✅ 质量检测: {quality.overall_score}/100")

                # LLM评分
                adjusted = self.style_manager.get_adjusted_criteria()
                hint = self.style_manager.get_style_prompt_hint()

                if self.grader is None:
                    raise RuntimeError("LLMGrader 未初始化")
                grader = self.grader

                if getattr(grader, "queue", None) is not None:
                    if not grader.queue._running:
                        grader.queue.start()

                score = grader.grade_with_style_to_dict(sections, hint, adjusted)
                report["score"] = score
                report["status"] = "已完成"
                print(f"  ✅ 评分完成: {score['total_score']}/{score['total_max']}")

                # 保存到数据库（非取消状态）
                if not self._grading_cancelled:
                    self.db.save_grading_record(
                        user_id=user.id,
                        filename=filename,
                        file_path=str(report["path"]),
                        score_data=score,
                        ocr_text=text,
                        sections=sections,
                        quality_score=quality.overall_score,
                        style_key=self.style_manager.get_current_style(),
                        style_name=self.style_manager.get_style_info()["name"],
                        is_aborted=False
                    )
                    print("  ✅ 已保存到数据库")

            except Exception as e:
                report["status"] = "失败"
                print(f"  ❌ 失败: {e}")

        print("\n" + "=" * 50)
        if self._grading_cancelled:
            print("批阅已取消")
        else:
            print("批阅完成！")
        print("=" * 50)

    def _stop_grading(self):
        """停止批阅"""
        if self._grading_thread is None or not self._grading_thread.is_alive():
            print("当前没有正在批阅的任务")
            return

        self._grading_cancelled = True
        if self.grader:
            self.grader.stop_queue()
        print("⚠️ 正在停止批阅...")
        self._grading_thread.join(timeout=5)

        # 重置未完成的报告状态
        for name, report in self.reports.items():
            if report["status"] in ("正在处理", "待处理"):
                report["status"] = "待处理"

    def _show_results(self):
        """显示批阅结果"""
        if not self.reports:
            print("暂无报告")
            return

        for name, data in self.reports.items():
            if not data.get("score"):
                continue

            score = data["score"]
            print(f"\n{'='*50}")
            print(f"  文件: {name}")
            print(f"  总分: {score['total_score']}/{score['total_max']} ({score['percentage']}%)")
            print(f"  评语: {score['overall_comment'][:100]}...")
            print(f"{'='*50}")

    # ========== 导出 ==========

    def _export_excel(self):
        """导出Excel"""
        completed = [(n, d) for n, d in self.reports.items()
                    if d.get("score") and d["status"] == "已完成"]
        if not completed:
            print("没有已完成的报告")
            return

        path = OUTPUT_DIR / f"grading_results_{int(time.time())}.xlsx"
        scores = [d["score"] for _, d in completed]
        names = [n for n, _ in completed]
        self.exporter.export_excel(scores, names, path)
        print(f"✅ Excel已导出: {path}")

    def _export_pdf(self):
        """导出PDF"""
        completed = [(n, d) for n, d in self.reports.items()
                    if d.get("score") and d["status"] == "已完成"]
        if not completed:
            print("没有已完成的报告")
            return

        path = OUTPUT_DIR / f"grading_report_{int(time.time())}.pdf"
        scores = [d["score"] for _, d in completed]
        names = [n for n, _ in completed]
        self.exporter.export_pdf(scores, names, path)
        print(f"✅ PDF已导出: {path}")

    # ========== 历史记录 ==========

    def _show_history(self):
        """查看历史记录"""
        user = self.auth.get_current_user()
        if not user:
            return

        page = 1
        while True:
            result = self.history.get_user_history(user.id, page=page, page_size=10)
            records = result["records"]

            if not records:
                print("暂无历史记录")
                return

            print(self.history.format_history_summary(records))
            print(f"\n统计: 共{result['statistics']['total_count']}条 | "
                  f"平均{result['statistics']['avg_percentage']:.1f}%")

            if result["has_more"]:
                more = input("\n查看下一页? (y/n): ").strip().lower()
                if more == "y":
                    page += 1
                    continue
            break

    def _export_history(self):
        """导出历史记录"""
        user = self.auth.get_current_user()
        if not user:
            return

        path = OUTPUT_DIR / f"history_{user.username}_{int(time.time())}.xlsx"
        if self.history.export_history_to_excel(user.id, path):
            print(f"✅ 历史记录已导出: {path}")
        else:
            print("❌ 导出失败")

    def _regrade_history(self):
        """重新批阅历史报告"""
        user = self.auth.get_current_user()
        if not user:
            return

        filename = input("请输入要重新批阅的文件名: ").strip()
        record = self.history.get_record_for_regrading(user.id, filename)

        if not record:
            print("❌ 未找到该文件的历史记录")
            return

        print(f"找到历史记录: {record.filename}")
        print(f"上次评分: {record.total_score}/{record.total_max}")
        confirm = input("确认重新批阅? (y/n): ").strip().lower()
        if confirm != "y":
            return

        # 加载到当前会话
        self.reports[filename] = {
            "path": Path(record.file_path) if record.file_path else INPUT_DIR / filename,
            "text": record.ocr_text,
            "sections": record.sections,
            "score": None,
            "status": "待处理",
            "quality": None,
        }

        # 重新批阅（不使用缓存）
        self._grading_cancelled = False
        if self.grader:
            self.grader.reset_cancel()

        report = self.reports[filename]
        ext = report["path"].suffix.lower()

        print(f"\n重新批阅: {filename}")

        try:
            # 如果已有OCR文本，跳过OCR
            if not report["text"]:
                if ext == ".pdf":
                    pages = self.ocr.process_pdf(report["path"])
                    report["text"] = "\n\n".join(pages)
                elif ext == ".docx":
                    report["text"] = self.ocr.process_docx(report["path"])
                elif ext == ".doc":
                    report["text"] = self.ocr.process_doc(report["path"])
                elif ext in SUPPORTED_IMAGE_FORMATS:
                    report["text"] = self.ocr.recognize(report["path"])

            # 如果已有结构化数据，跳过
            if not report["sections"]:
                cleaned = self.structurer.clean_text(report["text"])
                report["sections"] = self.structurer.extract_sections(cleaned)

            # 质量检测
            image_path = report["path"] if ext in SUPPORTED_IMAGE_FORMATS else None
            quality = self.quality_checker.full_check(filename, image_path, report["text"], report["sections"])
            report["quality"] = quality

            # LLM评分（不使用缓存）
            if not self.grader:
                raise RuntimeError("LLM评分器未初始化")

            adjusted = self.style_manager.get_adjusted_criteria()
            hint = self.style_manager.get_style_prompt_hint()

            if not self.grader.queue._running:
                self.grader.queue.start()

            score = self.grader.grade_with_style_to_dict(
                report["sections"], hint, adjusted, use_cache=False
            )
            report["score"] = score
            report["status"] = "已完成"

            print(f"✅ 重新批阅完成: {score['total_score']}/{score['total_max']}")

            # 更新数据库记录
            self.db.update_grading_record(
                record_id=record.id,
                score_data=score,
                ocr_text=report["text"],
                sections=report["sections"],
                quality_score=quality.overall_score,
                style_key=self.style_manager.get_current_style(),
                style_name=self.style_manager.get_style_info()["name"]
            )
            print(f"✅ 数据库记录已更新 (ID: {record.id})")

        except Exception as e:
            report["status"] = "失败"
            print(f"❌ 重新批阅失败: {e}")

    # ========== 其他功能 ==========

    def _switch_style(self):
        """切换批阅风格"""
        styles = self.style_manager.get_all_styles()
        print("\n可用风格:")
        for key, info in styles.items():
            marker = "✓" if key == self.style_manager.get_current_style() else " "
            print(f"  [{marker}] {key}: {info['name']} - {info['description']}")

        choice = input("\n输入风格key: ").strip()
        if self.style_manager.set_current_style(choice):
            print(f"✅ 已切换: {self.style_manager.get_style_info()['name']}")
        else:
            print("❌ 无效的风格")

    def _check_quality(self):
        """质量检测"""
        if not self.reports:
            print("暂无报告")
            return

        for name, report in self.reports.items():
            ext = report["path"].suffix.lower()
            image_path = report["path"] if ext in SUPPORTED_IMAGE_FORMATS else None
            quality = self.quality_checker.full_check(
                name, image_path, report.get("text"), report.get("sections")
            )
            report["quality"] = quality
            status = "✅" if quality.is_acceptable else "⚠️"
            print(f"{status} {name}: {quality.overall_score}/100")

    def _show_dashboard(self):
        """数据仪表盘（文本版）"""
        user = self.auth.get_current_user()
        if not user:
            return

        stats = self.db.get_user_statistics(user.id)
        print("\n" + "=" * 50)
        print("  数据仪表盘")
        print("=" * 50)
        print(f"  总记录数: {stats['total_count']}")
        print(f"  平均分:   {stats['avg_percentage']:.1f}%")
        print(f"  最高分:   {stats['max_percentage']:.1f}%")
        print(f"  最低分:   {stats['min_percentage']:.1f}%")
        print(f"  及格数:   {stats['pass_count']}")
        print(f"  优秀数:   {stats['excellent_count']}")
        print("=" * 50)

    def _show_statistics(self):
        """个人统计"""
        self._show_dashboard()

    def run(self):
        """运行CLI"""
        # 尝试自动登录
        user = self.auth.check_auto_login()
        if user:
            print(f"✅ 自动登录: {user.username}")
            self.show_main_menu()
        else:
            self.show_auth_menu()


def main():
    """主入口"""
    print("=" * 50)
    print("  实验报告批阅智能体 CLI v4.0")
    print("=" * 50)
    print("启动中...")

    cli = GradingCLI()
    cli.run()


if __name__ == "__main__":
    main()