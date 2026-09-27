"""
GUI界面模块（v4.0 - 新增：批阅风格、邮件发送、拖拽上传、仪表盘、质量检测、对比图表）
负责：tkinter界面、报告列表、结果展示、进度显示、主题、快捷键、最近文件、批量导入、
      批阅风格选择、邮件发送、文件拖拽、数据可视化仪表盘、质量检测
"""

import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext
import threading
import logging
import time

import json
from pathlib import Path
from datetime import datetime
from typing import Optional
from typing import Any

from matplotlib.backends._backend_tk import NavigationToolbar2Tk

from config import (
    OUTPUT_DIR, SUPPORTED_IMAGE_FORMATS,
    ALL_SUPPORTED_FORMATS, THEMES, DEFAULT_THEME, MAX_RECENT_FILES, RECENT_FILES
)
from modules.ocr_processor import OCRProcessor
from modules.text_structurer import TextStructurer
from modules.llm_grader import LLMGrader
from modules.exporter import ResultExporter
from modules.grading_style import GradingStyleManager
from modules.email_sender import EmailSender
from modules.dashboard import GradingDashboard
from modules.quality_checker import ReportQualityChecker
from modules.drag_drop import DragDropHandler
from modules.database import DatabaseManager
from modules.auth import AuthManager
from modules.history_manager import HistoryManager
from modules.agents import (
    IntelligentSummaryAgent, EmailDispatchAgent, BehaviorAwareAgent,
    TeachingAssistantAgent, ProgressWarningAgent,
)

logger = logging.getLogger(__name__)


class GraderGUI:
    """实验报告批阅系统 GUI v4.0"""

    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("实验报告批阅智能体 v4.0")
        self.root.geometry("1800x900")
        self.root.minsize(1200, 700)

        # 多用户系统
        self.db = DatabaseManager()
        self.auth = AuthManager(self.db)
        self.history_mgr = HistoryManager(self.db)
        self.summary_agent = IntelligentSummaryAgent()
        self.email_dispatch_agent = EmailDispatchAgent()
        self.behavior_agent = BehaviorAwareAgent()
        self.teaching_agent = TeachingAssistantAgent()
        self.progress_warning_agent = ProgressWarningAgent()
        self._session_login_logged = False

        # 数据
        self.reports = {}
        self.ocr = OCRProcessor()
        self.structurer = TextStructurer()
        self.exporter = ResultExporter()
        self.style_manager = GradingStyleManager()
        self.email_sender = EmailSender()
        self.dashboard = GradingDashboard()
        self.quality_checker = ReportQualityChecker(self.ocr)
        self.current_theme = DEFAULT_THEME
        # 历史记录对话框变量
        self.history_search_var = tk.StringVar()
        self.history_sort_by = tk.StringVar(value="按ID")
        self.history_sort_order = tk.StringVar(value="desc")
        self.history_select_all_var = tk.BooleanVar(value=False)
        self.history_tree = None
        self._current_history_records = []
        self.toolbar_buttons = []  # 确保存在
        
        # 批阅控制
        self._grading_thread = None
        self._grading_cancelled = False
        self._active_grading_filenames = []
        self._current_grading_filename = None

        # 尝试初始化评分器
        try:
            self.grader = LLMGrader()
            self.grader_available = True
        except ValueError:
            self.grader = None
            self.grader_available = False

        # 加载最近文件
        self.recent_files = self._load_recent_files()

        # 检查自动登录
        if self.auth.check_auto_login():
            self._show_main_ui()
        else:
            self._show_login_ui()

    def _log_behavior(self, event_type: str, metadata: Optional[dict] = None):
        """记录当前用户行为，供行为感知Agent推荐快捷入口。"""
        try:
            user = self.auth.get_current_user()
            if user:
                self.behavior_agent.log_event(user.id, event_type, metadata)
                if hasattr(self, "recommend_btn"):
                    self._refresh_recommended_entry()
        except Exception as e:
            logger.debug(f"行为记录失败: {e}")

    def _build_agent_style_hint(self) -> str:
        """融合批阅风格和智能助教画像提示。"""
        style_hint = self.style_manager.get_style_prompt_hint()
        user = self.auth.get_current_user()
        profile_hint = self.teaching_agent.build_profile_hint(user)
        if profile_hint:
            return f"{style_hint}\n\n{profile_hint}"
        return style_hint

    def _setup_dialog_window(self, dialog, width: int = 720, height: int = 560,
                             min_width: int = 520, min_height: int = 360,
                             modal: bool = True, resizable: bool = True):
        """统一设置对话框尺寸，避免小屏幕下内容和按钮被遮挡。"""
        try:
            screen_w = max(self.root.winfo_screenwidth(), 800)
            screen_h = max(self.root.winfo_screenheight(), 600)
            width = min(width, max(520, screen_w - 120))
            height = min(height, max(360, screen_h - 140))
            dialog.geometry(f"{width}x{height}")
            dialog.minsize(min(min_width, width), min(min_height, height))
            dialog.resizable(resizable, resizable)
            if modal:
                dialog.transient(self.root)
                dialog.grab_set()
        except Exception:
            pass

    def _bind_canvas_mousewheel(self, canvas: tk.Canvas, bind_widget=None):
        """给自定义滚动画布补齐鼠标滚轮支持（Windows/macOS/Linux）。"""
        bind_widget = bind_widget or canvas

        def _on_mousewheel(event):
            if getattr(event, "num", None) == 4:
                canvas.yview_scroll(-3, "units")
            elif getattr(event, "num", None) == 5:
                canvas.yview_scroll(3, "units")
            else:
                delta = getattr(event, "delta", 0)
                if delta:
                    canvas.yview_scroll(int(-1 * (delta / 120)), "units")
            return "break"

        def _bind(_event):
            canvas.bind_all("<MouseWheel>", _on_mousewheel)
            canvas.bind_all("<Button-4>", _on_mousewheel)
            canvas.bind_all("<Button-5>", _on_mousewheel)

        def _unbind(_event):
            canvas.unbind_all("<MouseWheel>")
            canvas.unbind_all("<Button-4>")
            canvas.unbind_all("<Button-5>")

        bind_widget.bind("<Enter>", _bind)
        bind_widget.bind("<Leave>", _unbind)

    def _create_scrollable_dialog_body(self, dialog, padding: str = "20"):
        """创建带滚轮的对话框主体，并保留固定底部按钮栏。"""
        theme = self._get_theme()
        footer = ttk.Frame(dialog, padding=(16, 10))
        footer.pack(side=tk.BOTTOM, fill=tk.X)

        body = ttk.Frame(dialog)
        body.pack(side=tk.TOP, fill=tk.BOTH, expand=True)
        canvas = tk.Canvas(body, bg=theme.get("bg", "#ffffff"), highlightthickness=0)
        scrollbar = ttk.Scrollbar(body, orient="vertical", command=canvas.yview)
        content = ttk.Frame(canvas, padding=padding)
        window_id = canvas.create_window((0, 0), window=content, anchor="nw")

        def _refresh_scrollregion(_event=None):
            canvas.configure(scrollregion=canvas.bbox("all"))

        def _sync_width(event):
            canvas.itemconfigure(window_id, width=event.width)

        content.bind("<Configure>", _refresh_scrollregion)
        canvas.bind("<Configure>", _sync_width)
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self._bind_canvas_mousewheel(canvas, body)
        return content, footer

    def _get_recent_history_records(self, limit: int = 1000):
        """获取当前用户近期历史记录。"""
        user = self.auth.get_current_user()
        if not user:
            return []
        try:
            result = self.history_mgr.get_user_history(user.id, page=1, page_size=limit)
            return result.get("records", [])
        except Exception as e:
            self._log(f"读取历史记录失败: {e}", "error")
            return []

    def _show_login_ui(self):
        """显示登录界面"""
        # 禁用拖拽（如果已启用）
        if hasattr(self, 'drag_drop') and self.drag_drop:
            try:
                self.drag_drop.disable()
            except Exception:
                pass
        # 清除现有内容
        for widget in self.root.winfo_children():
            widget.destroy()
        
        self.root.title("实验报告批阅智能体 - 登录")
        
        frame = ttk.Frame(self.root, padding="50")
        frame.place(relx=0.5, rely=0.5, anchor="center")
        
        ttk.Label(frame, text="实验报告批阅智能体", font=("微软雅黑", 20, "bold")).pack(pady=20)
        ttk.Label(frame, text="v4.0", font=("微软雅黑", 12)).pack()
        
        ttk.Label(frame, text="用户名:").pack(pady=(20, 5))
        # 获取所有用户名作为下拉选项
        usernames = self.auth.get_all_usernames() if hasattr(self.auth, 'get_all_usernames') else []
        self.login_user = ttk.Combobox(frame, width=28, values=usernames)
        self.login_user.pack()
        # 绑定选择事件：选择账号后清空密码框
        self.login_user.bind("<<ComboboxSelected>>", lambda e: self.login_pass.delete(0, tk.END))
        
        ttk.Label(frame, text="密码:").pack(pady=(10, 5))
        self.login_pass = ttk.Entry(frame, width=30, show="*")
        self.login_pass.pack()
        
        self.auto_login_var = tk.BooleanVar()
        ttk.Checkbutton(frame, text="自动登录", variable=self.auto_login_var).pack(pady=10)
        
        btn_frame = ttk.Frame(frame)
        btn_frame.pack(pady=20)
        ttk.Button(btn_frame, text="登录", command=self._do_login, width=12).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="注册", command=self._do_register, width=12).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="删除用户", command=self._do_delete_user, width=12).pack(side=tk.LEFT, padx=5)
        
        self.login_status = ttk.Label(frame, text="")
        self.login_status.pack()

    def _do_delete_user(self):
        """删除用户（连带删除所有相关数据）"""
        username = self.login_user.get().strip()
        if not username:
            self.login_status.configure(text="❌ 请先选择要删除的用户", foreground="red")
            return
        
        # 确认对话框
        if not messagebox.askyesno(
            "确认删除",
            f"确定要删除用户 '{username}' 吗？\n\n"
            f"此操作将永久删除：\n"
            f"• 该用户的所有批阅历史记录\n"
            f"• 该用户的个人资料信息\n"
            f"• 该用户的自动登录凭证\n\n"
            f"此操作不可恢复！"
        ):
            return
        
        # 执行删除
        success, msg = self.auth.delete_user(username)
        if success:
            # 刷新下拉框
            usernames = self.auth.get_all_usernames() if hasattr(self.auth, 'get_all_usernames') else []
            self.login_user['values'] = usernames
            self.login_user.set('')
            self.login_pass.delete(0, tk.END)
            self.login_status.configure(text=f"✅ {msg}", foreground="green")
        else:
            self.login_status.configure(text=f"❌ {msg}", foreground="red")
    
    def _do_login(self):
        """执行登录"""
        username = self.login_user.get().strip()
        password = self.login_pass.get().strip()
        auto = self.auto_login_var.get()
        
        success, msg = self.auth.login(username, password, auto_login=auto)
        if success:
            # 按当前用户初始化邮件发送器
            user = self.auth.get_current_user()
            if user:
                self.email_sender = EmailSender(user.id)
            self._show_main_ui()
        else:
            self.login_status.configure(text=f"❌ {msg}", foreground="red")
    
    def _do_register(self):
        """执行注册"""
        username = self.login_user.get().strip()
        password = self.login_pass.get().strip()
        
        if not username or not password:
            self.login_status.configure(text="❌ 用户名和密码不能为空", foreground="red")
            return
        
        success, msg = self.auth.register(username, password)
        self.login_status.configure(text=f"{'✅' if success else '❌'} {msg}", 
                                    foreground="green" if success else "red")
    
    def _show_main_ui(self):
        """显示主界面"""
        user = self.auth.get_current_user()
        if user and getattr(self.email_sender, 'user_id', None) != user.id:
            self.email_sender = EmailSender(user.id)
        # 禁用旧拖拽（如果存在）
        if hasattr(self, 'drag_drop') and self.drag_drop:
            try:
                self.drag_drop.disable()
            except Exception:
                pass
        for widget in self.root.winfo_children():
            widget.destroy()
        
        self.root.title("实验报告批阅智能体 v4.0")
        
        try:
            self._build_ui()
            self._apply_theme()
            self._bind_shortcuts()
            self._setup_drag_drop()
        except Exception as e:
            import traceback
            print(f"ERROR: {e}")
            traceback.print_exc()
            messagebox.showerror("错误", f"界面初始化失败:\n{e}")
            return
        
        user = self.auth.get_current_user()
        if user:
            self._log(f"欢迎, {user.username}!", "success")
            if not self._session_login_logged:
                self._log_behavior("login", {"auto_login": True})
                self._session_login_logged = True

    def _show_profile_dialog(self):
        """显示个人资料对话框"""
        self._log_behavior("profile")
        user = self.auth.get_current_user()
        if not user:
            return
        
        fresh_user = self.db.get_user_by_id(user.id)
        if fresh_user:
            user = fresh_user
            self.auth.current_user = fresh_user  # 同步更新
    
        dialog = tk.Toplevel(self.root)
        dialog.title("个人资料")
        self._setup_dialog_window(dialog, width=1000, height=720, min_width=760, min_height=520)
    
        theme = self._get_theme()
        dialog.configure(bg=theme["bg"])
    
        frame, footer = self._create_scrollable_dialog_body(dialog, padding="20")
    
        # 头像显示区域
        avatar_frame = ttk.Frame(frame)
        avatar_frame.pack(pady=10)

        # 创建固定大小的头像显示区域（方形，150x150）
        self._avatar_canvas = tk.Canvas(avatar_frame, width=150, height=150, 
                                        bg="#e5e7eb", highlightthickness=1,
                                        highlightbackground="#cccccc")
        self._avatar_canvas.pack()

        # 默认显示文字
        self._avatar_canvas.create_text(75, 75, text="无头像", 
                                        font=("微软雅黑", 12), fill="#666666")

        # 选择头像按钮
        ttk.Button(avatar_frame, text="选择头像", 
                command=lambda: self._select_avatar(dialog, user)).pack(pady=5)

        # 加载已有头像
        self._load_avatar_preview(user)
    
        # 表单字段
        fields = {}
        field_configs = [
            ("nickname", "昵称", getattr(user, 'nickname', '') or ''),
            ("email", "邮箱", getattr(user, 'email', '') or ''),
            ("student_id", "学工号", getattr(user, 'student_id', '') or ''),
            ("major", "专业", getattr(user, 'major', '') or ''),
            ("grade", "年级", getattr(user, 'grade', '') or ''),
            ("experiment_course", "实验课程", getattr(user, 'experiment_course', '') or ''),
            ("phone", "手机号", getattr(user, 'phone', '') or ''),
            ("bio", "个人简介", getattr(user, 'bio', '') or ''),
        ]
    
        for key, label, default in field_configs:
            ttk.Label(frame, text=f"{label}:").pack(anchor=tk.W, pady=(10, 2))
            if key == "bio":
                text = scrolledtext.ScrolledText(frame, wrap=tk.WORD, height=4)
                text.pack(fill=tk.X)
                text.insert(tk.END, default)
                fields[key] = text
            else:
                entry = ttk.Entry(frame, width=40)
                entry.pack(fill=tk.X)
                entry.insert(0, default)
                fields[key] = entry

        # 账户信息区域
        ttk.Separator(frame, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=15)

        account_frame = ttk.LabelFrame(frame, text="账户信息", padding="10")
        account_frame.pack(fill=tk.X, pady=(0, 10))

        ttk.Label(account_frame, text=f"用户名: {user.username}", font=("微软雅黑", 10)).pack(anchor=tk.W, pady=2)
        ttk.Label(account_frame, text=f"注册时间: {user.created_at.strftime('%Y-%m-%d %H:%M:%S') if user.created_at else '未知'}", font=("微软雅黑", 10)).pack(anchor=tk.W, pady=2)
    
        def on_save():
            update_data = {}
            for key, widget in fields.items():
                if key == "bio":
                    update_data[key] = widget.get(1.0, tk.END).strip()
                else:
                    update_data[key] = widget.get().strip()

            # 头像路径已经在 _select_avatar 中保存到数据库了，这里不需要重复处理
        
            if self.db.update_user_profile(user.id, **update_data):
                # 刷新当前用户对象
                self.auth.current_user = self.db.get_user_by_id(user.id)
                messagebox.showinfo("成功", "个人资料已更新")
                self._log("个人资料已更新", "success")
                dialog.destroy()
            else:
                messagebox.showerror("失败", "保存失败")
    
        ttk.Button(footer, text="保存", command=on_save).pack(side=tk.RIGHT, padx=5)
        ttk.Button(footer, text="关闭", command=dialog.destroy).pack(side=tk.RIGHT, padx=5)

    def _load_avatar_preview(self, user):
        """加载并显示用户头像预览"""
        avatar_path = getattr(user, 'avatar_path', None)
        if not avatar_path:
            return
        
        path = Path(avatar_path)
        if not path.exists():
            return
        
        try:
            from PIL import Image, ImageTk

            
            # 打开图片并缩放为 150x150
            img = Image.open(path)
            img = img.convert("RGB")
            img.thumbnail((150, 150), Image.Resampling.LANCZOS)
            
            # 居中裁剪为方形
            w, h = img.size
            size = min(w, h)
            left = (w - size) // 2
            top = (h - size) // 2
            img = img.crop((left, top, left + size, top + size))
            img = img.resize((150, 150), Image.Resampling.LANCZOS)
            
            # 显示到 Canvas
            photo = ImageTk.PhotoImage(img)
            self._avatar_canvas.delete("all")
            self._avatar_canvas.create_image(75, 75, image=photo)
            setattr(self._avatar_canvas, "_photo_ref", photo)  # 保持引用防止被GC
        except Exception as e:
            logger.warning(f"加载头像预览失败: {e}")

    def _select_avatar(self, parent_dialog, user):
        """选择头像图片并预览"""
        filepath = filedialog.askopenfilename(
            title="选择头像",
            filetypes=[("图片文件", "*.png;*.jpg;*.jpeg;*.gif;*.bmp")]
        )
        if not filepath:
            self._log("用户取消选择头像", "info")
            return
        
        if not user:
            messagebox.showwarning("提示", "请先登录")
            return
        
        self._log(f"选择头像: {filepath}", "info")
        self._log(f"当前用户: id={user.id}, username={user.username}", "info")
        
        try:
            from PIL import Image, ImageTk

            
            try:
                resample = Image.Resampling.LANCZOS
            except AttributeError:
                resample = Image.LANCZOS    # type: ignore
            
            img = Image.open(filepath)
            img = img.convert("RGB")
            
            img.thumbnail((150, 150), resample)
            w, h = img.size
            size = min(w, h)
            left = (w - size) // 2
            top = (h - size) // 2
            img = img.crop((left, top, left + size, top + size))
            img = img.resize((150, 150), resample)
            
            avatar_dir = Path(__file__).parent.parent / "data" / "avatars"
            avatar_dir.mkdir(parents=True, exist_ok=True)
            
            ext = ".jpg"
            dest = avatar_dir / f"avatar_user_{user.id}{ext}"
            
            self._log(f"头像保存路径: {dest}", "info")
            
            img.save(dest, "JPEG", quality=90)
            self._log("图片已保存到磁盘", "success")
            
            # 调用数据库更新
            self._log(f"调用 update_user_profile: user_id={user.id}, avatar_path={dest}", "info")
            result = self.db.update_user_profile(user.id, avatar_path=str(dest))
            self._log(f"update_user_profile 返回: {result}", "info")
            
            if result:
                photo = ImageTk.PhotoImage(img)
                self._avatar_canvas.delete("all")
                self._avatar_canvas.create_image(75, 75, image=photo)
                setattr(self._avatar_canvas, "_photo_ref", photo)
                self._log("头像已更新", "success")
            else:
                messagebox.showerror("失败", "头像保存到数据库失败")
                
        except Exception as e:
            messagebox.showerror("错误", f"处理头像失败: {e}")
            self._log(f"选择头像失败: {e}", "error")
            import traceback
            traceback.print_exc()

    # ========== 主题管理 ==========

    def _get_theme(self):
        return THEMES[self.current_theme]

    def _apply_theme(self):
        """应用当前主题到所有组件"""
        theme = self._get_theme()
        style = ttk.Style()
        style.theme_use("clam")

        style.configure(".",
                        background=theme["frame_bg"],
                        foreground=theme["label_fg"],
                        font=("微软雅黑", 10))
        style.configure("TFrame", background=theme["frame_bg"])
        style.configure("TLabel",
                        background=theme["frame_bg"],
                        foreground=theme["label_fg"],
                        font=("微软雅黑", 10))
        style.configure("TButton",
                        background=theme["button_bg"],
                        foreground=theme["button_fg"],
                        font=("微软雅黑", 10),
                        padding=5)
        style.map("TButton",
                  background=[("active", theme["button_active_bg"]),
                              ("pressed", theme["button_active_bg"])],
                  foreground=[("active", theme["button_fg"]),
                              ("pressed", theme["button_fg"])])
        style.configure("TNotebook", background=theme["bg"], tabmargins=[2, 5, 2, 0])
        style.configure("TNotebook.Tab",
                        background=theme["card_bg"],
                        foreground=theme["fg"],
                        font=("微软雅黑", 10),
                        padding=[10, 5])
        style.map("TNotebook.Tab",
                  background=[("selected", theme["accent"])],
                  foreground=[("selected", theme["button_fg"])],
                  expand=[("selected", [1, 1, 1, 0])])
        style.configure("Treeview",
                        background=theme["tree_bg"],
                        foreground=theme["tree_fg"],
                        fieldbackground=theme["tree_bg"],
                        rowheight=28,
                        font=("微软雅黑", 10))
        style.configure("Treeview.Heading",
                        background=theme["accent"],
                        foreground=theme["button_fg"],
                        font=("微软雅黑", 10, "bold"),
                        padding=5)
        style.map("Treeview",
                  background=[("selected", theme["tree_select_bg"])],
                  foreground=[("selected", theme["tree_select_fg"])])
        style.configure("Custom.TLabelframe",
                        background=theme["frame_bg"],
                        borderwidth=2,
                        relief="groove")
        style.configure("Custom.TLabelframe.Label",
                        background=theme["frame_bg"],
                        foreground=theme["labelframe_fg"],
                        font=("微软雅黑", 10, "bold"))
        style.configure("Horizontal.TProgressbar",
                        background=theme["progress_fg"],
                        troughcolor=theme["progress_bg"],
                        thickness=20)

        self.root.configure(bg=theme["bg"])

        for widget in [self.raw_text, self.dim_text, self.comment_text, self.log_text, self.quality_text]:
            widget.configure(
                bg=theme["text_bg"],
                fg=theme["text_fg"],
                insertbackground=theme["fg"],
                selectbackground=theme.get("text_select_bg", theme["accent"]),
                selectforeground=theme["text_fg"],
                font=("微软雅黑", 10)
            )

        menubar = self.root.cget("menu")
        if menubar:
            menu = self.root.nametowidget(menubar)
            self._update_menu_colors(menu, theme)

        self.status_label.configure(
            background=theme["frame_bg"],
            foreground=theme["label_fg"],
            font=("微软雅黑", 9)
        )
        self.api_label.configure(
            background=theme["frame_bg"],
            foreground=theme["success"] if self.grader_available else theme["error"],
            font=("微软雅黑", 10, "bold")
        )
        self.score_label.configure(
            background=theme["frame_bg"],
            foreground=theme["accent"],
            font=("微软雅黑", 24, "bold")
        )
        self.style_label.configure(
            background=theme["frame_bg"],
            foreground=theme["accent"],
            font=("微软雅黑", 9)
        )

        for btn in self.toolbar_buttons:
            btn.configure(style="TButton")

        self._log(f"已切换到{theme['name']}主题", "info")

    def _update_menu_colors(self, menu, theme):
        """递归更新菜单颜色"""
        menu.configure(
            bg=theme["card_bg"],
            fg=theme["fg"],
            activebackground=theme["accent"],
            activeforeground=theme["button_fg"]
        )
        last = menu.index(tk.END)
        if last is None:
            return
        for i in range(last + 1):
            try:
                item_type = menu.type(i)
                if item_type == "cascade":
                    submenu_name = menu.entrycget(i, "menu")
                    if submenu_name:
                        submenu = menu.nametowidget(submenu_name)
                        self._update_menu_colors(submenu, theme)
            except tk.TclError:
                pass

    def _toggle_theme(self):
        """切换主题"""
        self.current_theme = "dark" if self.current_theme == "light" else "light"
        self._apply_theme()

    # ========== 快捷键绑定 ==========

    def _bind_shortcuts(self):
        """绑定快捷键"""
        self.root.bind("<Control-o>", lambda e: self._import_reports())
        self.root.bind("<Control-O>", lambda e: self._import_reports())
        self.root.bind("<Control-e>", lambda e: self._export_excel())
        self.root.bind("<Control-E>", lambda e: self._export_excel())
        self.root.bind("<Control-p>", lambda e: self._export_pdf())
        self.root.bind("<Control-P>", lambda e: self._export_pdf())
        self.root.bind("<Control-g>", lambda e: self._start_grading())
        self.root.bind("<Control-G>", lambda e: self._start_grading())
        self.root.bind("<Control-f>", lambda e: self._import_folder())
        self.root.bind("<Control-F>", lambda e: self._import_folder())
        self.root.bind("<Control-t>", lambda e: self._toggle_theme())
        self.root.bind("<Control-T>", lambda e: self._toggle_theme())
        self.root.bind("<Control-m>", lambda e: self._show_email_dialog())
        self.root.bind("<Control-M>", lambda e: self._show_email_dialog())
        self.root.bind("<Control-d>", lambda e: self._show_dashboard())
        self.root.bind("<Control-D>", lambda e: self._show_dashboard())
        self.root.bind("<Delete>", lambda e: self._remove_selected())
        self.root.bind("<Control-s>", lambda e: self._stop_grading())
        self.root.bind("<Control-S>", lambda e: self._stop_grading())
        self.root.bind("<Control-r>", lambda e: self._regrade_selected())
        self.root.bind("<Control-R>", lambda e: self._regrade_selected())
        self.root.bind("<Control-Shift-G>", lambda e: self._continue_grading())
        self.root.bind("<Control-Shift-g>", lambda e: self._continue_grading())

    # ========== 拖拽上传 ==========

    def _setup_drag_drop(self):
        """设置文件拖拽"""
        self.drag_drop = DragDropHandler(self.root, self._on_files_dropped)
        self.drag_drop.enable()
        if self.drag_drop.is_enabled():
            self._log("文件拖拽功能已启用（直接将文件拖入窗口即可上传）", "success")
        else:
            self._log("文件拖拽功能未启用（请使用导入按钮）", "warning")

    def _on_files_dropped(self, filepaths: list):
        """处理拖拽的文件"""
        count = 0
        skipped = 0
        for filepath in filepaths:
            path = Path(filepath)
            if path.suffix.lower() in ALL_SUPPORTED_FORMATS:
                if self._import_single_file(path):
                    count += 1
                else:
                    skipped += 1
            else:
                skipped += 1

        msg = f"拖拽导入完成：成功 {count} 个"
        if skipped > 0:
            msg += f"，跳过 {skipped} 个（不支持的格式或重复）"
        self._log(msg, "success")

    # ========== 最近文件管理 ==========

    def _load_recent_files(self) -> list:
        if RECENT_FILES.exists():
            try:
                with open(RECENT_FILES, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    return data.get("files", [])
            except Exception as e:
                logger.warning(f"加载最近文件列表失败: {e}")
                return []
        return []

    def _save_recent_files(self):
        try:
            RECENT_FILES.parent.mkdir(parents=True, exist_ok=True)
            with open(RECENT_FILES, 'w', encoding='utf-8') as f:
                json.dump({"files": self.recent_files}, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.warning(f"保存最近文件列表失败: {e}")

    def _add_to_recent(self, filepath: str):
        if filepath in self.recent_files:
            self.recent_files.remove(filepath)
        self.recent_files.insert(0, filepath)
        self.recent_files = self.recent_files[:MAX_RECENT_FILES]
        self._save_recent_files()
        self._update_recent_menu()

    def _update_recent_menu(self):
        self.recent_menu.delete(0, tk.END)
        if not self.recent_files:
            self.recent_menu.add_command(label="(无最近文件)", state=tk.DISABLED)
        else:
            for filepath in self.recent_files:
                path = Path(filepath)
                display = f"{path.name}"
                self.recent_menu.add_command(
                    label=display,
                    command=lambda fp=filepath: self._import_from_recent(fp)
                )
            self.recent_menu.add_separator()
            self.recent_menu.add_command(label="清空最近文件", command=self._clear_recent)
        theme = self._get_theme()
        self._update_menu_colors(self.recent_menu, theme)

    def _import_from_recent(self, filepath: str):
        path = Path(filepath)
        if path.exists():
            self._import_single_file(path)
        else:
            messagebox.showwarning("文件不存在", f"文件已不存在: {filepath}")
            self.recent_files.remove(filepath)
            self._save_recent_files()
            self._update_recent_menu()

    def _clear_recent(self):
        self.recent_files = []
        self._save_recent_files()
        self._update_recent_menu()
        self._log("最近文件列表已清空", "warning")

    def _logout(self):
        """退出登录"""
        if messagebox.askyesno("确认", "确定要退出登录吗？"):
            # 禁用拖拽
            if hasattr(self, 'drag_drop') and self.drag_drop:
                try:
                    self.drag_drop.disable()
                except Exception:
                    pass

            self.auth.logout()
            self._session_login_logged = False
            self.reports.clear()
            for widget in self.root.winfo_children():
                widget.destroy()
            self.email_sender = EmailSender()  # 重置为无用户状态
            self._show_login_ui()

    def _switch_user(self):
        """切换用户：保存当前会话，回到登录界面"""
        # 禁用拖拽
        if hasattr(self, 'drag_drop') and self.drag_drop:
            try:
                self.drag_drop.disable()
            except Exception:
                pass
        # 可选：保存当前报告列表状态（如果需要）
        self._session_login_logged = False
        self.reports.clear()
        for widget in self.root.winfo_children():
            widget.destroy()
        self.email_sender = EmailSender()  # 重置，新用户登录后自动加载其配置
        self._show_login_ui()

    # ========== 构建UI ==========

    def _build_ui(self):
        """构建界面 v4.0"""
        # 菜单栏
        menubar = tk.Menu(self.root)
        self.root.config(menu=menubar)

        # 文件菜单
        file_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="文件", menu=file_menu)
        file_menu.add_command(label="导入报告...  Ctrl+O", command=self._import_reports)
        file_menu.add_command(label="导入文件夹...  Ctrl+F", command=self._import_folder)
        file_menu.add_separator()
        file_menu.add_command(label="导出Excel  Ctrl+E", command=self._export_excel)
        file_menu.add_command(label="导出PDF  Ctrl+P", command=self._export_pdf)
        file_menu.add_separator()
        self.recent_menu = tk.Menu(file_menu, tearoff=0)
        file_menu.add_cascade(label="最近文件", menu=self.recent_menu)
        self._update_recent_menu()
        file_menu.add_separator()
        file_menu.add_command(label="退出", command=self.root.quit)

        # 操作菜单
        action_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="操作", menu=action_menu)
        action_menu.add_command(label="开始批阅  Ctrl+G", command=self._start_grading)
        action_menu.add_command(label="停止批阅  Ctrl+S", command=self._stop_grading)
        action_menu.add_command(label="继续批阅  Ctrl+Shift+G", command=self._continue_grading)
        action_menu.add_command(label="重新批阅  Ctrl+R", command=self._regrade_selected)
        action_menu.add_command(label="删除选中  Delete", command=self._remove_selected)
        action_menu.add_separator()
        action_menu.add_command(label="质量检测", command=self._check_quality_selected)
        action_menu.add_command(label="批量质量检测", command=self._check_quality_all)
        action_menu.add_separator()
        action_menu.add_command(label="清空缓存", command=self._clear_cache)

        # 风格菜单
        style_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="批阅风格", menu=style_menu)
        self._build_style_menu(style_menu)

        # 邮件菜单
        email_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="邮件", menu=email_menu)
        email_menu.add_command(label="发送结果...  Ctrl+M", command=self._show_email_dialog)
        email_menu.add_command(label="配置SMTP", command=self._show_smtp_config)
        email_menu.add_separator()
        email_menu.add_command(label="测试连接", command=self._test_email_connection)

        # 分析菜单
        analysis_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="分析", menu=analysis_menu)
        analysis_menu.add_command(label="数据仪表盘  Ctrl+D", command=self._show_dashboard)
        analysis_menu.add_command(label="智能摘要Agent", command=self._show_summary_agent)
        analysis_menu.add_command(label="行为推荐Agent", command=self._show_behavior_agent)
        analysis_menu.add_command(label="进度预警Agent", command=self._show_progress_warning_agent)
        analysis_menu.add_command(label="批量对比图表", command=self._show_comparison_chart)
        analysis_menu.add_separator()
        analysis_menu.add_command(label="清空历史记录", command=self._clear_history)

        # 视图菜单
        view_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="视图", menu=view_menu)
        view_menu.add_command(label="切换主题  Ctrl+T", command=self._toggle_theme)

        # 用户菜单
        user_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="用户", menu=user_menu)
        user_menu.add_command(label="个人资料", command=self._show_profile_dialog)
        user_menu.add_separator()
        user_menu.add_command(label="切换用户", command=self._switch_user)
        user_menu.add_command(label="退出登录", command=self._logout)
        
        # 历史记录菜单
        history_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="历史", menu=history_menu)
        history_menu.add_command(label="查看历史记录", command=self._show_history_dialog)
        history_menu.add_command(label="导出历史记录", command=self._export_history_dialog)

        # 帮助菜单
        help_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="帮助", menu=help_menu)
        help_menu.add_command(label="快捷键说明", command=self._show_shortcuts)
        help_menu.add_command(label="关于", command=self._show_about)

        # 主框架
        main_frame = ttk.Frame(self.root, padding="10")
        main_frame.grid(row=0, column=0, sticky="nsew")
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)
        main_frame.columnconfigure(1, weight=1)
        main_frame.rowconfigure(1, weight=1)

        # === 顶部工具栏 ===
        toolbar = ttk.Frame(main_frame)
        toolbar.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 10))

        self.toolbar_buttons = []

        btn_import = ttk.Button(toolbar, text="📁 导入报告", command=self._import_reports)
        btn_import.pack(side=tk.LEFT, padx=5)
        self.toolbar_buttons.append(btn_import)

        btn_folder = ttk.Button(toolbar, text="📂 导入文件夹", command=self._import_folder)
        btn_folder.pack(side=tk.LEFT, padx=5)
        self.toolbar_buttons.append(btn_folder)

        btn_grade = ttk.Button(toolbar, text="▶ 开始批阅", command=self._start_grading)
        btn_grade.pack(side=tk.LEFT, padx=5)
        self.toolbar_buttons.append(btn_grade)

        btn_stop = ttk.Button(toolbar, text="⏹ 停止批阅", command=self._stop_grading)
        btn_stop.pack(side=tk.LEFT, padx=5)
        self.toolbar_buttons.append(btn_stop)
        self.btn_stop = btn_stop  # 保存引用以便启用/禁用

        btn_continue = ttk.Button(toolbar, text="▶️ 继续批阅", command=self._continue_grading)
        btn_continue.pack(side=tk.LEFT, padx=5)
        self.toolbar_buttons.append(btn_continue)
        self.btn_continue = btn_continue

        btn_regrade = ttk.Button(toolbar, text="🔄 重新批阅", command=self._regrade_selected)
        btn_regrade.pack(side=tk.LEFT, padx=5)
        self.toolbar_buttons.append(btn_regrade)

        btn_excel = ttk.Button(toolbar, text="📊 导出Excel", command=self._export_excel)
        btn_excel.pack(side=tk.LEFT, padx=5)
        self.toolbar_buttons.append(btn_excel)

        btn_pdf = ttk.Button(toolbar, text="📄 导出PDF", command=self._export_pdf)
        btn_pdf.pack(side=tk.LEFT, padx=5)
        self.toolbar_buttons.append(btn_pdf)

        btn_ppt = ttk.Button(toolbar, text="📽 导出PPT", command=self._export_ppt)
        btn_ppt.pack(side=tk.LEFT, padx=5)
        self.toolbar_buttons.append(btn_ppt)

        btn_docx = ttk.Button(toolbar, text="📝 导出DOCX", command=self._export_docx)
        btn_docx.pack(side=tk.LEFT, padx=5)
        self.toolbar_buttons.append(btn_docx)

        btn_email = ttk.Button(toolbar, text="📧 发送邮件", command=self._show_email_dialog)
        btn_email.pack(side=tk.LEFT, padx=5)
        self.toolbar_buttons.append(btn_email)

        btn_dashboard = ttk.Button(toolbar, text="📈 仪表盘", command=self._show_dashboard)
        btn_dashboard.pack(side=tk.LEFT, padx=5)
        self.toolbar_buttons.append(btn_dashboard)

        btn_agents = ttk.Button(toolbar, text="🧠 Agent中心", command=self._show_agent_center)
        btn_agents.pack(side=tk.LEFT, padx=5)
        self.toolbar_buttons.append(btn_agents)

        self._recommended_action_key = None
        self.recommend_btn = ttk.Button(toolbar, text="✨ 推荐入口", command=self._run_top_recommended_action)
        self.recommend_btn.pack(side=tk.LEFT, padx=5)
        self.toolbar_buttons.append(self.recommend_btn)

        btn_theme = ttk.Button(toolbar, text="🌓 切换主题", command=self._toggle_theme)
        btn_theme.pack(side=tk.LEFT, padx=5)
        self.toolbar_buttons.append(btn_theme)

        # 风格选择下拉框
        ttk.Label(toolbar, text="批阅风格:").pack(side=tk.LEFT, padx=(20, 5))
        self.style_var = tk.StringVar(value="standard")
        self.style_combo = ttk.Combobox(toolbar, textvariable=self.style_var,
                                         values=[s[1] for s in self.style_manager.get_style_display_list()],
                                         width=15, state="readonly")
        self.style_combo.pack(side=tk.LEFT, padx=5)
        self.style_combo.bind("<<ComboboxSelected>>", self._on_style_changed)

        # 当前风格显示
        self.style_label = ttk.Label(toolbar, text="标准风格")
        self.style_label.pack(side=tk.LEFT, padx=5)
        self._refresh_recommended_entry()

        # API状态
        api_status = "✅ API已连接" if self.grader_available else "❌ API未配置"
        self.api_label = ttk.Label(toolbar, text=api_status)
        self.api_label.pack(side=tk.RIGHT, padx=10)

        # === 左侧：报告列表 ===
        left_frame = ttk.LabelFrame(main_frame, text="报告列表", padding="5", style="Custom.TLabelframe")
        left_frame.grid(row=1, column=0, sticky="nsew", padx=(0, 10))
        left_frame.columnconfigure(0, weight=1)
        left_frame.rowconfigure(0, weight=1)

        columns = ("filename", "status", "score", "quality")
        self.tree = ttk.Treeview(left_frame, columns=columns, show="headings", selectmode="browse")
        self.tree.heading("filename", text="文件名")
        self.tree.heading("status", text="状态")
        self.tree.heading("score", text="分数")
        self.tree.heading("quality", text="质量")
        self.tree.column("filename", width=180)
        self.tree.column("status", width=70)
        self.tree.column("score", width=60)
        self.tree.column("quality", width=60)
        self.tree.grid(row=0, column=0, sticky="nsew")

        scrollbar = ttk.Scrollbar(left_frame, orient="vertical", command=self.tree.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=scrollbar.set)

        self.tree.bind("<<TreeviewSelect>>", self._on_select)

        # === 右侧：详情面板（Notebook）===
        right_frame = ttk.Notebook(main_frame)
        right_frame.grid(row=1, column=1, sticky="nsew")

        # -- 评分结果页 --
        result_tab = ttk.Frame(right_frame, padding="10")
        right_frame.add(result_tab, text="评分结果")

        self.score_frame = ttk.LabelFrame(result_tab, text="总分", padding="10", style="Custom.TLabelframe")
        self.score_frame.pack(fill=tk.X, pady=(0, 10))
        self.score_label = ttk.Label(self.score_frame, text="未评分")
        self.score_label.pack()

        self.dim_frame = ttk.LabelFrame(result_tab, text="各维度评分", padding="10", style="Custom.TLabelframe")
        self.dim_frame.pack(fill=tk.BOTH, expand=True, pady=(0, 10))
        self.dim_text = scrolledtext.ScrolledText(self.dim_frame, wrap=tk.WORD, height=10)
        self.dim_text.pack(fill=tk.BOTH, expand=True)

        self.comment_frame = ttk.LabelFrame(result_tab, text="总体评语与建议", padding="10", style="Custom.TLabelframe")
        self.comment_frame.pack(fill=tk.BOTH, expand=True)
        self.comment_text = scrolledtext.ScrolledText(self.comment_frame, wrap=tk.WORD, height=6)
        self.comment_text.pack(fill=tk.BOTH, expand=True)

        # -- 原文查看页 --
        raw_tab = ttk.Frame(right_frame, padding="10")
        right_frame.add(raw_tab, text="OCR原文")
        self.raw_text = scrolledtext.ScrolledText(raw_tab, wrap=tk.WORD)
        self.raw_text.pack(fill=tk.BOTH, expand=True)

        # -- 质量检测页 --
        quality_tab = ttk.Frame(right_frame, padding="10")
        right_frame.add(quality_tab, text="质量检测")
        self.quality_text = scrolledtext.ScrolledText(quality_tab, wrap=tk.WORD, state=tk.DISABLED)
        self.quality_text.pack(fill=tk.BOTH, expand=True)

        # -- 日志页 --
        log_tab = ttk.Frame(right_frame, padding="10")
        right_frame.add(log_tab, text="系统日志")
        self.log_text = scrolledtext.ScrolledText(log_tab, wrap=tk.WORD, state=tk.DISABLED)
        self.log_text.pack(fill=tk.BOTH, expand=True)

        # === 底部：进度条 ===
        bottom_frame = ttk.Frame(main_frame)
        bottom_frame.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(10, 0))

        self.progress = ttk.Progressbar(bottom_frame, mode="determinate", style="Horizontal.TProgressbar")
        self.progress.pack(fill=tk.X, pady=(0, 5))

        self.status_label = ttk.Label(bottom_frame, text="就绪 | 拖拽文件到窗口可直接上传")
        self.status_label.pack(anchor=tk.W)

    def _build_style_menu(self, menu):
        """构建批阅风格菜单"""
        styles = self.style_manager.get_all_styles()

        for key, info in styles.items():
            prefix = "✓ " if key == self.style_manager.get_current_style() else "    "
            menu.add_command(
                label=f"{prefix}{info['name']}",
                command=lambda k=key: self._set_style(k)
            )

        menu.add_separator()
        menu.add_command(label="自定义风格...", command=self._show_custom_style_dialog)
        menu.add_command(label="管理自定义风格", command=self._manage_custom_styles)

    def _on_style_changed(self, event=None):
        """风格下拉框改变"""
        display_list = self.style_manager.get_style_display_list()
        selected = self.style_var.get()
        for key, display in display_list:
            if display == selected:
                self._set_style(key)
                break

    def _set_style(self, style_key: str):
        """设置批阅风格"""
        if self.style_manager.set_current_style(style_key):
            info = self.style_manager.get_style_info(style_key)
            self.style_label.configure(text=info["name"])
            self._log(f"已切换批阅风格: {info['name']} - {info['description']}", "success")

            # 更新菜单勾选状态
            menubar = self.root.cget("menu")
            if menubar:
                menu = self.root.nametowidget(menubar)
                for i in range(menu.index(tk.END) + 1):
                    try:
                        if menu.entrycget(i, "label") == "批阅风格":
                            submenu_name = menu.entrycget(i, "menu")
                            submenu = menu.nametowidget(submenu_name)
                            submenu.delete(0, tk.END)
                            self._build_style_menu(submenu)
                            break
                    except tk.TclError:
                        pass

            # 更新下拉框
            display_list = self.style_manager.get_style_display_list()
            self.style_combo['values'] = [s[1] for s in display_list]
            for key, display in display_list:
                if key == style_key:
                    self.style_var.set(display)
                    break
        else:
            self._log(f"切换风格失败: {style_key}", "error")

    # ========== 批阅风格对话框 ==========

    def _show_custom_style_dialog(self):
        """显示自定义风格对话框"""
        dialog = tk.Toplevel(self.root)
        dialog.title("创建自定义批阅风格")
        self._setup_dialog_window(dialog, width=640, height=620, min_width=520, min_height=430)

        theme = self._get_theme()
        dialog.configure(bg=theme["bg"])

        frame = ttk.Frame(dialog, padding="20")
        frame.pack(fill=tk.BOTH, expand=True)

        ttk.Label(frame, text="风格标识（英文）:").grid(row=0, column=0, sticky=tk.W, pady=5)
        key_entry = ttk.Entry(frame, width=30)
        key_entry.grid(row=0, column=1, sticky=tk.EW, pady=5)

        ttk.Label(frame, text="显示名称:").grid(row=1, column=0, sticky=tk.W, pady=5)
        name_entry = ttk.Entry(frame, width=30)
        name_entry.grid(row=1, column=1, sticky=tk.EW, pady=5)

        ttk.Label(frame, text="描述:").grid(row=2, column=0, sticky=tk.W, pady=5)
        desc_entry = ttk.Entry(frame, width=30)
        desc_entry.grid(row=2, column=1, sticky=tk.EW, pady=5)

        ttk.Label(frame, text="严格度 (0.5-1.5):").grid(row=3, column=0, sticky=tk.W, pady=5)
        strictness_var = tk.DoubleVar(value=1.0)
        strictness_scale = ttk.Scale(frame, from_=0.5, to=1.5, variable=strictness_var, orient=tk.HORIZONTAL)
        strictness_scale.grid(row=3, column=1, sticky=tk.EW, pady=5)
        strictness_label = ttk.Label(frame, text="1.0")
        strictness_label.grid(row=3, column=2, padx=5)
        strictness_scale.configure(command=lambda v, lbl_ref=strictness_label: lbl_ref.configure(text=f"{float(v):.2f}"))

        ttk.Label(frame, text="语气风格:").grid(row=4, column=0, sticky=tk.W, pady=5)
        tone_var = tk.StringVar(value="客观、严谨")
        tone_combo = ttk.Combobox(frame, textvariable=tone_var,
                                   values=["客观、严谨", "严厉、直接", "温和、鼓励", "专业、学术", "务实、工程化"],
                                   width=28, state="readonly")
        tone_combo.grid(row=4, column=1, sticky=tk.EW, pady=5)

        ttk.Label(frame, text="评语风格:").grid(row=5, column=0, sticky=tk.W, pady=5)
        comment_var = tk.StringVar(value="标准")
        comment_combo = ttk.Combobox(frame, textvariable=comment_var,
                                      values=["标准", "严厉", "鼓励", "学术", "工程"],
                                      width=28, state="readonly")
        comment_combo.grid(row=5, column=1, sticky=tk.EW, pady=5)

        ttk.Label(frame, text="维度权重调整:", font=("微软雅黑", 10, "bold")).grid(row=6, column=0, columnspan=2, sticky=tk.W, pady=(15, 5))

        weight_vars = {}
        row = 7
        for dim_name in ["实验目的理解", "实验步骤完整性", "结果分析与数据处理", "结论与总结"]:
            ttk.Label(frame, text=f"{dim_name}:").grid(row=row, column=0, sticky=tk.W, pady=3)
            var = tk.DoubleVar(value=1.0)
            scale = ttk.Scale(frame, from_=0.5, to=1.5, variable=var, orient=tk.HORIZONTAL)
            scale.grid(row=row, column=1, sticky=tk.EW, pady=3)
            lbl = ttk.Label(frame, text="1.0")
            lbl.grid(row=row, column=2, padx=5)
            scale.configure(command=lambda v, lbl_ref=lbl: lbl_ref.configure(text=f"{float(v):.2f}"))
            weight_vars[dim_name] = var
            row += 1

        frame.columnconfigure(1, weight=1)

        def on_create():
            key = key_entry.get().strip()
            name = name_entry.get().strip()
            desc = desc_entry.get().strip()

            if not key or not name:
                messagebox.showwarning("提示", "请填写风格标识和显示名称")
                return

            weight_adjustments = {}
            for dim_name, var in weight_vars.items():
                val = var.get()
                if abs(val - 1.0) > 0.01:
                    weight_adjustments[dim_name] = round(val, 2)

            success = self.style_manager.create_custom_style(
                key=key,
                name=name,
                description=desc,
                strictness=round(strictness_var.get(), 2),
                weight_adjustments=weight_adjustments,
                tone=tone_var.get(),
                comment_style=comment_var.get(),
            )

            if success:
                self._log(f"已创建自定义风格: {name}", "success")
                self._set_style(key)
                dialog.destroy()
            else:
                messagebox.showerror("错误", "风格标识已存在或是预设风格")

        btn_frame = ttk.Frame(frame)
        btn_frame.grid(row=row, column=0, columnspan=3, pady=20)
        ttk.Button(btn_frame, text="创建", command=on_create).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="取消", command=dialog.destroy).pack(side=tk.LEFT, padx=5)

    def _manage_custom_styles(self):
        """管理自定义风格"""
        dialog = tk.Toplevel(self.root)
        dialog.title("管理自定义风格")
        self._setup_dialog_window(dialog, width=480, height=380, min_width=420, min_height=300)

        frame = ttk.Frame(dialog, padding="10")
        frame.pack(fill=tk.BOTH, expand=True)

        ttk.Label(frame, text="自定义风格列表:", font=("微软雅黑", 10, "bold")).pack(anchor=tk.W, pady=(0, 10))

        listbox = tk.Listbox(frame, height=10)
        listbox.pack(fill=tk.BOTH, expand=True)

        scrollbar = ttk.Scrollbar(listbox, orient="vertical", command=listbox.yview)
        listbox.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        custom = self.style_manager.custom_styles
        for key, info in custom.items():
            listbox.insert(tk.END, f"{info['name']} ({key})")

        def on_delete():
            selection = listbox.curselection()
            if not selection:
                return
            item = listbox.get(selection[0])
            key = item.split("(")[-1].rstrip(")")

            if messagebox.askyesno("确认", f"确定删除风格 '{item}' 吗？"):
                if self.style_manager.delete_custom_style(key):
                    listbox.delete(selection[0])
                    self._log(f"已删除自定义风格: {key}", "success")
                    menubar = self.root.cget("menu")
                    if menubar:
                        menu = self.root.nametowidget(menubar)
                        for i in range(menu.index(tk.END) + 1):
                            try:
                                if menu.entrycget(i, "label") == "批阅风格":
                                    submenu_name = menu.entrycget(i, "menu")
                                    submenu = menu.nametowidget(submenu_name)
                                    submenu.delete(0, tk.END)
                                    self._build_style_menu(submenu)
                                    break
                            except tk.TclError:
                                pass

        ttk.Button(frame, text="删除选中", command=on_delete).pack(pady=10)

    # ========== 邮件功能 ==========

    def _show_smtp_config(self):
        """显示SMTP配置对话框"""
        dialog = tk.Toplevel(self.root)
        dialog.title("SMTP邮箱配置")
        self._setup_dialog_window(dialog, width=640, height=520, min_width=520, min_height=400)

        # === 根据主题设置对话框背景 ===
        theme = self._get_theme()
        dialog.configure(bg=theme["bg"])

        frame = ttk.Frame(dialog, padding="20")
        frame.pack(fill=tk.BOTH, expand=True)

        # === 添加这段：深色主题下用 tk.Entry 替代 ttk.Entry，并设置颜色 ===
        is_dark = self.current_theme == "dark"
        entry_bg = theme.get("text_bg", "#ffffff")
        entry_fg = theme.get("text_fg", "#000000")

        ttk.Label(frame, text="SMTP服务器:", font=("微软雅黑", 10, "bold")).grid(row=0, column=0, sticky=tk.W, pady=5)
        if is_dark:
            server_entry = tk.Entry(frame, width=35, bg=entry_bg, fg=entry_fg)
        else:
            server_entry = ttk.Entry(frame, width=35)
        server_entry.grid(row=0, column=1, sticky=tk.EW, pady=5)
        server_entry.insert(0, "smtp.qq.com")

        ttk.Label(frame, text="SMTP端口:").grid(row=1, column=0, sticky=tk.W, pady=5)
        if is_dark:
            port_entry = tk.Entry(frame, width=35, bg=entry_bg, fg=entry_fg)
        else:
            port_entry = ttk.Entry(frame, width=35)
        port_entry.grid(row=1, column=1, sticky=tk.EW, pady=5)
        port_entry.insert(0, "465")

        ttk.Label(frame, text="发件人邮箱:").grid(row=2, column=0, sticky=tk.W, pady=5)
        if is_dark:
            email_entry = tk.Entry(frame, width=35, bg=entry_bg, fg=entry_fg)
        else:
            email_entry = ttk.Entry(frame, width=35)
        email_entry.grid(row=2, column=1, sticky=tk.EW, pady=5)

        ttk.Label(frame, text="授权码/密码:").grid(row=3, column=0, sticky=tk.W, pady=5)
        if is_dark:
            password_entry = tk.Entry(frame, width=35, bg=entry_bg, fg=entry_fg, show="*")
        else:
            password_entry = ttk.Entry(frame, width=35, show="*")
        password_entry.grid(row=3, column=1, sticky=tk.EW, pady=5)

        use_ssl_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(frame, text="使用SSL", variable=use_ssl_var).grid(row=4, column=0, columnspan=2, sticky=tk.W, pady=5)

        ttk.Label(frame, text="提示: QQ邮箱请使用授权码而非登录密码", foreground="gray").grid(row=5, column=0, columnspan=2, sticky=tk.W, pady=5)
        ttk.Label(frame, text="常用服务器: QQ(smtp.qq.com:465), 163(smtp.163.com:465), Gmail(smtp.gmail.com:587)", foreground="gray", wraplength=400).grid(row=6, column=0, columnspan=2, sticky=tk.W, pady=5)

        frame.columnconfigure(1, weight=1)

        def on_save():
            server = server_entry.get().strip()
            port_str = port_entry.get().strip()
            email = email_entry.get().strip()
            password = password_entry.get().strip()

            if not all([server, port_str, email, password]):
                messagebox.showwarning("提示", "请填写所有字段")
                return

            try:
                port = int(port_str)
            except ValueError:
                messagebox.showerror("错误", "端口必须是数字")
                return

            self.email_sender.configure(server, port, email, password, use_ssl_var.get())
            self._log(f"SMTP配置已保存: {email}", "success")
            dialog.destroy()

        def on_test():
            on_save()
            self._test_email_connection()

        btn_frame = ttk.Frame(frame)
        btn_frame.grid(row=7, column=0, columnspan=2, pady=20)
        ttk.Button(btn_frame, text="保存", command=on_save).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="保存并测试", command=on_test).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="取消", command=dialog.destroy).pack(side=tk.LEFT, padx=5)

    def _test_email_connection(self):
        """测试邮件连接"""
        success, msg = self.email_sender.test_connection()
        if success:
            messagebox.showinfo("连接测试", f"✅ {msg}")
            self._log("SMTP连接测试成功", "success")
        else:
            messagebox.showerror("连接测试", f"❌ {msg}")
            self._log(f"SMTP连接测试失败: {msg}", "error")

    def _show_email_dialog(self):
        """显示发送邮件对话框"""
        self._log_behavior("email")
        completed_items = [(name, data) for name, data in self.reports.items()
                          if data.get("score") and data.get("status") == "已完成"]
        if not completed_items:
            messagebox.showwarning("警告", "没有已完成的评分结果可发送！")
            return

        if not self.email_sender.is_configured():
            if not messagebox.askyesno("未配置", "SMTP未配置，是否现在配置？"):
                return
            self._show_smtp_config()
            return

        dialog = tk.Toplevel(self.root)
        dialog.title("发送批阅结果")
        self._setup_dialog_window(dialog, width=720, height=620, min_width=560, min_height=460)
        theme = self._get_theme()
        dialog.configure(bg=theme["bg"])

        frame, footer = self._create_scrollable_dialog_body(dialog, padding="20")

        ttk.Label(frame, text="发送批阅结果", font=("微软雅黑", 14, "bold")).pack(anchor=tk.W, pady=(0, 15))

        ttk.Label(frame, text="选择附件:").pack(anchor=tk.W, pady=(5, 2))
        attach_frame = ttk.Frame(frame)
        attach_frame.pack(fill=tk.X, pady=2)

        attach_var = tk.StringVar(value="excel")
        ttk.Radiobutton(attach_frame, text="Excel", variable=attach_var, value="excel").pack(side=tk.LEFT, padx=5)
        ttk.Radiobutton(attach_frame, text="PDF", variable=attach_var, value="pdf").pack(side=tk.LEFT, padx=5)
        ttk.Radiobutton(attach_frame, text="PPT", variable=attach_var, value="ppt").pack(side=tk.LEFT, padx=5)
        ttk.Radiobutton(attach_frame, text="Word", variable=attach_var, value="docx").pack(side=tk.LEFT, padx=5)

        ttk.Label(frame, text="收件人邮箱:").pack(anchor=tk.W, pady=(10, 2))
        recipient_entry = ttk.Entry(frame, width=50)
        recipient_entry.pack(fill=tk.X, pady=2)
        recipient_entry.insert(0, "")

        ttk.Label(frame, text="收件人称呼:").pack(anchor=tk.W, pady=(5, 2))
        name_entry = ttk.Entry(frame, width=50)
        name_entry.pack(fill=tk.X, pady=2)
        name_entry.insert(0, "老师/同学")

        score_context = self._build_email_score_context(completed_items)
        mail_template = self.email_dispatch_agent.compose("老师/同学", score_context, attachment_label="批阅结果")

        ttk.Label(frame, text="邮件主题:").pack(anchor=tk.W, pady=(5, 2))
        subject_entry = ttk.Entry(frame, width=50)
        subject_entry.pack(fill=tk.X, pady=2)
        subject_entry.insert(0, mail_template["subject"])

        agent_tip = ttk.Label(frame, text=f"🤖 邮件智能分发Agent已根据成绩判断紧急程度：{mail_template['urgency']}", foreground="gray")
        agent_tip.pack(anchor=tk.W, pady=(5, 2))

        ttk.Label(frame, text="邮件正文（可编辑）:").pack(anchor=tk.W, pady=(6, 2))
        body_text = scrolledtext.ScrolledText(frame, wrap=tk.WORD, height=8)
        body_text.pack(fill=tk.BOTH, expand=True, pady=2)
        body_text.insert(tk.END, mail_template["body"])

        def refresh_ai_mail():
            display_name = name_entry.get().strip() or "老师/同学"
            template = self.email_dispatch_agent.compose(display_name, score_context, attachment_label="批阅结果")
            subject_entry.delete(0, tk.END)
            subject_entry.insert(0, template["subject"])
            body_text.delete(1.0, tk.END)
            body_text.insert(tk.END, template["body"])
            agent_tip.configure(text=f"🤖 邮件智能分发Agent已根据成绩判断紧急程度：{template['urgency']}")

        def on_send():
            recipient = recipient_entry.get().strip()
            name = name_entry.get().strip() or "老师/同学"
            body = body_text.get(1.0, tk.END).strip()
            subject = subject_entry.get().strip()
            
            if not recipient:
                messagebox.showwarning("提示", "请填写收件人邮箱")
                return

            attach_type = attach_var.get()
            temp_filename = f"temp_email_grading_{int(time.time())}"
            
            # 格式映射
            ext_map = {
                "excel": ".xlsx",
                "pdf": ".pdf", 
                "ppt": ".pptx",
                "docx": ".docx"
            }
            ext = ext_map.get(attach_type, ".xlsx")
            temp_path = OUTPUT_DIR / f"{temp_filename}{ext}"
            
            filenames = [name for name, _ in completed_items]
            scores = [data["score"] for _, data in completed_items]
            
            # 根据格式导出
            if attach_type == "excel":
                self.exporter.export_excel(scores, filenames, temp_path)
            elif attach_type == "pdf":
                self.exporter.export_pdf(scores, filenames, temp_path)
            elif attach_type == "ppt":
                self.exporter.export_ppt(scores, filenames, temp_path)
            elif attach_type == "docx":
                self.exporter.export_docx(scores, filenames, temp_path)

            success, msg = self.email_sender.send_grading_result(
                recipient_email=recipient,
                recipient_name=name,
                attachment_path=temp_path,
                subject=subject,
                message_body=body
            )

            if success:
                messagebox.showinfo("成功", msg)
                self._log(f"邮件已发送至 {recipient}（附件: {attach_type}）", "success")
                dialog.destroy()
            else:
                messagebox.showerror("失败", msg)
                self._log(f"发送邮件失败: {msg}", "error")

        ttk.Button(footer, text="取消", command=dialog.destroy).pack(side=tk.RIGHT, padx=5)
        ttk.Button(footer, text="发送", command=on_send).pack(side=tk.RIGHT, padx=5)
        ttk.Button(footer, text="🤖 重新生成智能邮件", command=refresh_ai_mail).pack(side=tk.LEFT, padx=5)

    # ========== 质量检测 ==========

    def _check_quality_selected(self):
        """检测选中报告的质量"""
        selection = self.tree.selection()
        if not selection:
            messagebox.showinfo("提示", "请先选择要检测的报告")
            return

        filename = self.tree.item(selection[0], "values")[0]
        report = self.reports.get(filename)
        if not report:
            return

        self._log(f"正在检测报告质量: {filename}...")
        
        image_path = None
        if report["path"].suffix.lower() in SUPPORTED_IMAGE_FORMATS:
            image_path = report["path"]
            self._log(f"质量检测: 图片格式 {report['path'].name}")
        else:
            self._log(f"质量检测: 非图片格式({report['path'].suffix})，OCR置信度使用文本质量分替代")
        
        result = self.quality_checker.full_check(
            filename=filename,
            image_path=image_path,
            text=report.get("text"),
            sections=report.get("sections")
        )

        self._display_quality_result(result)
        
        quality_text = "✅" if result.is_acceptable else "⚠️"
        for item in self.tree.get_children():
            if self.tree.item(item, "values")[0] == filename:
                values = list(self.tree.item(item, "values"))
                values[3] = f"{quality_text} {result.overall_score}"
                self.tree.item(item, values=tuple(values))
                break

        self._log(f"质量检测完成: {filename} - 综合得分 {result.overall_score}", 
                  "success" if result.is_acceptable else "warning")

    def _check_quality_all(self):
        """批量检测所有报告质量"""
        if not self.reports:
            messagebox.showinfo("提示", "没有可检测的报告")
            return

        self._log("开始批量质量检测...")
        
        for filename, report in self.reports.items():
            image_path = None
            if report["path"].suffix.lower() in SUPPORTED_IMAGE_FORMATS:
                image_path = report["path"]
            
            result = self.quality_checker.full_check(
                filename=filename,
                image_path=image_path,
                text=report.get("text"),
                sections=report.get("sections")
            )

            quality_text = "✅" if result.is_acceptable else "⚠️"
            for item in self.tree.get_children():
                if self.tree.item(item, "values")[0] == filename:
                    values = list(self.tree.item(item, "values"))
                    values[3] = f"{quality_text} {result.overall_score}"
                    self.tree.item(item, values=tuple(values))
                    break

        self._log("批量质量检测完成", "success")
        messagebox.showinfo("完成", "所有报告质量检测已完成！")

    def _display_quality_result(self, result):
        """在质量检测页显示结果"""
        self.quality_text.configure(state=tk.NORMAL)
        self.quality_text.delete(1.0, tk.END)
        
        status = "✅ 质量合格" if result.is_acceptable else "⚠️ 质量不合格"
        self.quality_text.insert(tk.END, f"文件: {result.filename}\n")
        self.quality_text.insert(tk.END, f"状态: {status}\n")
        self.quality_text.insert(tk.END, f"综合得分: {result.overall_score}/100\n")
        self.quality_text.insert(tk.END, f"OCR置信度: {result.ocr_confidence}%\n")
        self.quality_text.insert(tk.END, f"文本质量: {result.text_quality_score}/100\n")
        self.quality_text.insert(tk.END, f"结构完整性: {result.structure_score}/100\n\n")
        
        if result.warnings:
            self.quality_text.insert(tk.END, "【警告】\n")
            for w in result.warnings:
                self.quality_text.insert(tk.END, f"  ⚠️ {w}\n")
            self.quality_text.insert(tk.END, "\n")
        
        if result.suggestions:
            self.quality_text.insert(tk.END, "【建议】\n")
            for s in result.suggestions:
                self.quality_text.insert(tk.END, f"  💡 {s}\n")
            self.quality_text.insert(tk.END, "\n")
        
        if result.details:
            self.quality_text.insert(tk.END, "【详细数据】\n")
            self.quality_text.insert(tk.END, json.dumps(result.details, ensure_ascii=False, indent=2))
        
        self.quality_text.configure(state=tk.DISABLED)

    # ========== 数据可视化仪表盘 ==========

    def _show_dashboard(self):
        """显示数据可视化仪表盘窗口"""
        # 先清空仪表盘历史，避免重复计入
        self.dashboard.clear_history()
        # 重新从当前报告列表构建历史
        for name, data in self.reports.items():
            if data.get("score") and data.get("status") == "已完成":
                self.dashboard.add_record(name, data["score"])

        dialog = tk.Toplevel(self.root)
        dialog.title("数据可视化仪表盘")
        dialog.geometry("1200x800")
        dialog.transient(self.root)

        stats = self.dashboard.get_statistics()
        
        if not stats:
            ttk.Label(dialog, text="暂无评分数据，请先完成批阅", font=("微软雅黑", 14)).pack(pady=50)
            return

        notebook = ttk.Notebook(dialog, padding="10")
        notebook.pack(fill=tk.BOTH, expand=True)

        # === 统计概览页 ===
        overview_tab = ttk.Frame(notebook, padding="10")
        notebook.add(overview_tab, text="📊 统计概览")

        cards_frame = ttk.Frame(overview_tab)
        cards_frame.pack(fill=tk.X, pady=(0, 20))

        stat_items = [
            ("总报告数", f"{stats.get('total_reports', 0)}", "#3b82f6"),
            ("平均分", f"{stats.get('avg_score', 0)}%", "#22c55e"),
            ("中位数", f"{stats.get('median_score', 0)}%", "#8b5cf6"),
            ("最高分", f"{stats.get('max_score', 0)}%", "#f59e0b"),
            ("最低分", f"{stats.get('min_score', 0)}%", "#ef4444"),
            ("及格率", f"{stats.get('pass_rate', 0)}%", "#10b981"),
            ("优秀率", f"{stats.get('excellent_rate', 0)}%", "#06b6d4"),
            ("标准差", f"{stats.get('std_dev', 0)}", "#6366f1"),
        ]

        for i, (label, value, color) in enumerate(stat_items):
            card = ttk.LabelFrame(cards_frame, text=label, padding="10")
            card.grid(row=i // 4, column=i % 4, padx=5, pady=5, sticky="nsew")
            ttk.Label(card, text=value, font=("微软雅黑", 18, "bold"), foreground=color).pack()

        for i in range(4):
            cards_frame.columnconfigure(i, weight=1)

        dim_frame = ttk.LabelFrame(overview_tab, text="各维度平均分", padding="10")
        dim_frame.pack(fill=tk.X, pady=10)

        dim_data = stats.get("dimension_averages", {})
        if dim_data:
            dim_text = ""
            for name, avg in dim_data.items():
                bar = "█" * int(avg / 5)
                dim_text += f"{name}: {avg}% {bar}\n"
            ttk.Label(dim_frame, text=dim_text, font=("微软雅黑", 11), justify=tk.LEFT).pack(anchor=tk.W)

        # === 分数分布页 ===
        dist_tab = ttk.Frame(notebook, padding="10")
        notebook.add(dist_tab, text="📈 分数分布")

        canvas1 = self.dashboard.create_score_distribution_chart(dist_tab, self.current_theme)
        if canvas1:
            canvas1.get_tk_widget().pack(fill=tk.BOTH, expand=True)
            toolbar1 = NavigationToolbar2Tk(canvas1, dist_tab)
            toolbar1.update()

        # === 维度雷达图页 ===
        radar_tab = ttk.Frame(notebook, padding="10")
        notebook.add(radar_tab, text="🎯 维度分析")

        canvas2 = self.dashboard.create_dimension_radar_chart(radar_tab, self.current_theme)
        if canvas2:
            canvas2.get_tk_widget().pack(fill=tk.BOTH, expand=True)
            toolbar2 = NavigationToolbar2Tk(canvas2, radar_tab)
            toolbar2.update()

        # === 趋势分析页 ===
        trend_tab = ttk.Frame(notebook, padding="10")
        notebook.add(trend_tab, text="📉 趋势分析")

        canvas3 = self.dashboard.create_trend_chart(trend_tab, self.current_theme)
        if canvas3:
            canvas3.get_tk_widget().pack(fill=tk.BOTH, expand=True)
            toolbar3 = NavigationToolbar2Tk(canvas3, trend_tab)
            toolbar3.update()
        else:
            ttk.Label(trend_tab, text="需要至少2条评分记录才能显示趋势", font=("微软雅黑", 12)).pack(pady=50)

    # ========== 智能Agent中心 ==========

    def _show_agent_center(self):
        """显示智能Agent中心。"""
        self._log_behavior("summary_agent")
        dialog = tk.Toplevel(self.root)
        dialog.title("智能Agent中心")
        dialog.geometry("1200x800")
        dialog.transient(self.root)

        notebook = ttk.Notebook(dialog, padding="10")
        notebook.pack(fill=tk.BOTH, expand=True)

        summary_tab = ttk.Frame(notebook, padding="10")
        behavior_tab = ttk.Frame(notebook, padding="10")
        warning_tab = ttk.Frame(notebook, padding="10")
        assistant_tab = ttk.Frame(notebook, padding="10")

        notebook.add(summary_tab, text="📊 智能摘要")
        notebook.add(behavior_tab, text="⚡ 行为推荐")
        notebook.add(warning_tab, text="🚨 进度预警")
        notebook.add(assistant_tab, text="👩‍🏫 智能助教")

        self._build_summary_agent_tab(summary_tab)
        self._build_behavior_agent_tab(behavior_tab)
        self._build_progress_warning_tab(warning_tab)
        self._build_teaching_assistant_tab(assistant_tab)

    def _build_summary_agent_tab(self, parent):
        top = ttk.Frame(parent)
        top.pack(fill=tk.X, pady=(0, 8))
        ttk.Label(top, text="分析周期:").pack(side=tk.LEFT, padx=(0, 5))
        period_var = tk.StringVar(value="semester")
        period_combo = ttk.Combobox(
            top,
            textvariable=period_var,
            values=["month", "semester", "all"],
            state="readonly",
            width=12,
        )
        period_combo.pack(side=tk.LEFT, padx=5)
        ttk.Label(top, text="month=月度，semester=学期，all=全部").pack(side=tk.LEFT, padx=10)

        body = ttk.Frame(parent)
        body.pack(fill=tk.BOTH, expand=True)
        body.columnconfigure(0, weight=1)
        body.columnconfigure(1, weight=1)
        body.rowconfigure(0, weight=1)

        text_frame = ttk.LabelFrame(body, text="文字总结", padding="8")
        text_frame.grid(row=0, column=0, sticky="nsew", padx=(0, 5))
        text = scrolledtext.ScrolledText(text_frame, wrap=tk.WORD)
        text.pack(fill=tk.BOTH, expand=True)

        chart_frame = ttk.LabelFrame(body, text="能力雷达图", padding="8")
        chart_frame.grid(row=0, column=1, sticky="nsew", padx=(5, 0))

        def refresh():
            for child in chart_frame.winfo_children():
                child.destroy()
            records = self._get_recent_history_records()
            summary = self.summary_agent.summarize(records, period_var.get())
            text.configure(state=tk.NORMAL)
            text.delete(1.0, tk.END)
            text.insert(tk.END, summary.get("text", ""))
            text.configure(state=tk.DISABLED)
            canvas = self.summary_agent.create_radar_chart(chart_frame, summary, self.current_theme)
            if canvas:
                canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)
                toolbar = NavigationToolbar2Tk(canvas, chart_frame)
                toolbar.update()
            else:
                ttk.Label(chart_frame, text="暂无维度数据，完成批阅后会自动生成雷达图。", font=("微软雅黑", 12)).pack(pady=50)

        ttk.Button(top, text="生成摘要", command=refresh).pack(side=tk.LEFT, padx=5)
        period_combo.bind("<<ComboboxSelected>>", lambda e: refresh())
        refresh()

    def _build_behavior_agent_tab(self, parent):
        user = self.auth.get_current_user()
        if not user:
            ttk.Label(parent, text="请先登录").pack(pady=50)
            return
        analysis = self.behavior_agent.analyze(user.id)

        ttk.Label(parent, text="行为感知Agent会根据登录时段、操作频率和常用功能推荐首页快捷入口。", font=("微软雅黑", 11, "bold")).pack(anchor=tk.W, pady=(0, 8))

        info = scrolledtext.ScrolledText(parent, wrap=tk.WORD, height=7)
        info.pack(fill=tk.X, pady=(0, 10))
        info.insert(tk.END, analysis.get("summary", ""))
        info.insert(tk.END, "\n\n" + analysis.get("preload_hint", ""))
        info.configure(state=tk.DISABLED)

        action_frame = ttk.LabelFrame(parent, text="推荐快捷入口", padding="10")
        action_frame.pack(fill=tk.X, pady=8)
        for item in analysis.get("quick_actions", []):
            row = ttk.Frame(action_frame)
            row.pack(fill=tk.X, pady=4)
            ttk.Button(row, text=item.title, width=16, command=lambda k=item.action_key: self._run_recommended_action(k)).pack(side=tk.LEFT, padx=(0, 8))
            ttk.Label(row, text=item.reason).pack(side=tk.LEFT)

        events = analysis.get("events", [])[-20:]
        log_frame = ttk.LabelFrame(parent, text="最近行为记录", padding="10")
        log_frame.pack(fill=tk.BOTH, expand=True, pady=8)
        log = scrolledtext.ScrolledText(log_frame, wrap=tk.WORD)
        log.pack(fill=tk.BOTH, expand=True)
        if events:
            for event in reversed(events):
                log.insert(tk.END, f"{event.get('timestamp')}  {event.get('label', event.get('event_type'))}\n")
        else:
            log.insert(tk.END, "暂无行为记录。")
        log.configure(state=tk.DISABLED)

    def _build_progress_warning_tab(self, parent):
        records = self._get_recent_history_records()
        user = self.auth.get_current_user()
        result = self.progress_warning_agent.analyze(records, user)
        text = scrolledtext.ScrolledText(parent, wrap=tk.WORD)
        text.pack(fill=tk.BOTH, expand=True)
        text.insert(tk.END, result.get("text", ""))
        text.configure(state=tk.DISABLED)

    def _build_teaching_assistant_tab(self, parent):
        user = self.auth.get_current_user()
        description = self.teaching_agent.describe_profile(user)
        ttk.Label(parent, text="智能助教Agent会把个人资料中的专业、年级、实验课程融入批阅提示。", font=("微软雅黑", 11, "bold")).pack(anchor=tk.W, pady=(0, 8))
        text = scrolledtext.ScrolledText(parent, wrap=tk.WORD)
        text.pack(fill=tk.BOTH, expand=True)
        text.insert(tk.END, description)
        text.insert(tk.END, "\n\n提示：可在“用户 → 个人资料”中补充专业、年级和实验课程。下次批阅时，系统会自动调整评分侧重点和评语风格。")
        text.configure(state=tk.DISABLED)
        ttk.Button(parent, text="打开个人资料", command=self._show_profile_dialog).pack(anchor=tk.E, pady=8)

    def _show_summary_agent(self):
        self._log_behavior("summary_agent")
        dialog = tk.Toplevel(self.root)
        dialog.title("智能摘要Agent")
        dialog.geometry("1100x760")
        dialog.transient(self.root)
        frame = ttk.Frame(dialog, padding="10")
        frame.pack(fill=tk.BOTH, expand=True)
        self._build_summary_agent_tab(frame)

    def _show_behavior_agent(self):
        dialog = tk.Toplevel(self.root)
        dialog.title("行为推荐Agent")
        dialog.geometry("950x650")
        dialog.transient(self.root)
        frame = ttk.Frame(dialog, padding="10")
        frame.pack(fill=tk.BOTH, expand=True)
        self._build_behavior_agent_tab(frame)

    def _show_progress_warning_agent(self):
        dialog = tk.Toplevel(self.root)
        dialog.title("进度预警Agent")
        dialog.geometry("900x620")
        dialog.transient(self.root)
        frame = ttk.Frame(dialog, padding="10")
        frame.pack(fill=tk.BOTH, expand=True)
        self._build_progress_warning_tab(frame)

    def _refresh_recommended_entry(self):
        """刷新首页/工具栏上的个性化推荐入口。"""
        try:
            user = self.auth.get_current_user()
            if not user or not hasattr(self, "recommend_btn"):
                return
            analysis = self.behavior_agent.analyze(user.id)
            quick_actions = analysis.get("quick_actions", [])
            if quick_actions:
                top = quick_actions[0]
                self._recommended_action_key = top.action_key
                self.recommend_btn.configure(text=f"✨ 推荐：{top.title}")
            else:
                self._recommended_action_key = "summary_agent"
                self.recommend_btn.configure(text="✨ 推荐：智能摘要")
        except Exception as e:
            logger.debug(f"刷新推荐入口失败: {e}")

    def _run_top_recommended_action(self):
        """执行当前推荐入口。"""
        action_key = getattr(self, "_recommended_action_key", None) or "summary_agent"
        self._run_recommended_action(action_key)

    def _run_recommended_action(self, action_key: str):
        actions = {
            "import_reports": self._import_reports,
            "import_folder": self._import_folder,
            "start_grading": self._start_grading,
            "export_excel": self._export_excel,
            "export_pdf": self._export_pdf,
            "export_ppt": self._export_ppt,
            "export_docx": self._export_docx,
            "email": self._show_email_dialog,
            "history": self._show_history_dialog,
            "dashboard": self._show_dashboard,
            "summary_agent": self._show_summary_agent,
            "profile": self._show_profile_dialog,
        }
        func = actions.get(action_key)
        if func:
            func()
        else:
            messagebox.showinfo("提示", f"暂未绑定该快捷动作: {action_key}")

    def _log_progress_alert_if_needed(self):
        """批阅完成后静默写入进度预警日志。"""
        try:
            records = self._get_recent_history_records()
            user = self.auth.get_current_user()
            result = self.progress_warning_agent.analyze(records, user)
            if result.get("level") in {"high", "medium"}:
                self._log("🚨 进度预警Agent发现需要关注的情况，可在“分析 → 进度预警Agent”查看详情。", "warning")
        except Exception as e:
            logger.debug(f"进度预警分析失败: {e}")

    def _build_email_score_context(self, completed_items):
        """将一个或多个批阅结果合成为邮件智能分发Agent可用的成绩上下文。"""
        scores = [data.get("score", {}) for _, data in completed_items]
        if not scores:
            return {"percentage": 0, "strengths": [], "weaknesses": [], "overall_suggestions": ""}
        if len(scores) == 1:
            return scores[0]

        percentages = [float(s.get("percentage") or 0) for s in scores]
        strengths = []
        weaknesses = []
        suggestions = []
        for score in scores:
            strengths.extend(score.get("strengths", []) or [])
            weaknesses.extend(score.get("weaknesses", []) or [])
            if score.get("overall_suggestions"):
                suggestions.append(str(score.get("overall_suggestions")))
        return {
            "percentage": round(sum(percentages) / len(percentages), 1),
            "total_score": round(sum(float(s.get("total_score") or 0) for s in scores), 1),
            "total_max": round(sum(float(s.get("total_max") or 100) for s in scores), 1),
            "strengths": strengths[:5],
            "weaknesses": weaknesses[:5],
            "overall_suggestions": "\n".join(suggestions[:3]) or "请查看附件中的详细批阅建议。",
        }

    def _show_comparison_chart(self):
        """显示批量评分对比图表"""
        completed_items = [(name, data) for name, data in self.reports.items()
                          if data.get("score") and data.get("status") == "已完成"]
        if not completed_items:
            messagebox.showwarning("警告", "没有已完成的评分结果！")
            return

        dialog = tk.Toplevel(self.root)
        dialog.title("批量评分对比")
        dialog.geometry("1000x700")
        dialog.transient(self.root)

        frame = ttk.Frame(dialog, padding="10")
        frame.pack(fill=tk.BOTH, expand=True)

        scores_data = [data["score"] for _, data in completed_items]
        
        canvas = self.dashboard.create_comparison_chart(frame, scores_data, self.current_theme)
        if canvas:
            canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)
            toolbar = NavigationToolbar2Tk(canvas, frame)
            toolbar.update()

    def _clear_history(self):
        """清空历史记录"""
        if messagebox.askyesno("确认", "确定清空所有评分历史记录吗？"):
            self.dashboard.clear_history()
            self._log("评分历史记录已清空", "success")

    # ========== 核心功能方法 ==========

    def _log(self, message: str, level: str = "info"):
        """添加日志（带颜色）"""
        # 如果 log_text 尚未创建（如登录界面阶段），只记录到 logger
        if not hasattr(self, "log_text") or self.log_text is None:
            logger.info(message)
            return

        theme = self._get_theme()
        timestamp = datetime.now().strftime("%H:%M:%S")

        color_map = {
            "info": theme["log_info"],
            "success": theme["log_success"],
            "warning": theme["log_warning"],
            "error": theme["log_error"]
        }
        color = color_map.get(level, theme["log_info"])

        self.log_text.configure(state=tk.NORMAL)
        self.log_text.insert(tk.END, f"[{timestamp}] ")
        self.log_text.insert(tk.END, f"{message}\n", level)
        self.log_text.tag_config(level, foreground=color)
        self.log_text.see(tk.END)
        self.log_text.configure(state=tk.DISABLED)
        logger.info(message)

    def _import_single_file(self, filepath: Path):
        """导入单个文件"""
        filename = filepath.name
        if filename in self.reports:
            return False

        # 检测 .docx 文件是否真的是 docx 格式（.doc 伪装成 .docx 的情况，记录真实格式）
        real_ext = filepath.suffix.lower()
        if real_ext == '.docx':
            with open(filepath, 'rb') as f:
                header = f.read(4)
            if header == b'\xd0\xcf\x11\xe0':
                # 实际是 .doc 格式，但保持原始路径不变
                self._log(f"检测到 '{filename}' 实际是 .doc 格式，将自动转换处理", "info")
                real_ext = '.doc'  # 只改逻辑上的后缀判断，不改实际路径

        self.reports[filename] = {
            "path": filepath,
            "text": "",
            "sections": {},
            "score": None,
            "status": "待处理",
            "quality": None,
        }

        self.tree.insert("", tk.END, values=(filename, "待处理", "-", "-"))
        self._add_to_recent(str(filepath))
        return True

    def _import_reports(self):
        """导入报告文件（多选）"""
        self._log_behavior("import_reports")
        filetypes = [
            ("所有支持的格式", "*.png;*.jpg;*.jpeg;*.bmp;*.tiff;*.webp;*.gif;*.pdf;*.docx;*.doc"),
            ("图片文件", "*.png;*.jpg;*.jpeg;*.bmp;*.tiff;*.webp;*.gif"),
            ("PDF文档", "*.pdf"),
            ("Word文档", "*.docx;*.doc"),
            ("所有文件", "*.*")
        ]

        files = filedialog.askopenfilenames(title="选择实验报告", filetypes=filetypes)
        if not files:
            return

        count = 0
        for filepath in files:
            if self._import_single_file(Path(filepath)):
                count += 1

        self._log(f"成功导入 {count} 个报告文件", "success")

    def _import_folder(self):
        """导入整个文件夹"""
        self._log_behavior("import_folder")
        folder = filedialog.askdirectory(title="选择包含实验报告的文件夹")
        if not folder:
            return

        folder_path = Path(folder)
        count = 0
        skipped = 0

        for file_path in folder_path.rglob("*"):
            if file_path.is_file() and file_path.suffix.lower() in ALL_SUPPORTED_FORMATS:
                if self._import_single_file(file_path):
                    count += 1
                else:
                    skipped += 1

        msg = f"文件夹导入完成：成功 {count} 个"
        if skipped > 0:
            msg += f"，跳过重复 {skipped} 个"
        self._log(msg, "success")

    def _remove_selected(self):
        """删除选中的报告"""
        selection = self.tree.selection()
        if not selection:
            messagebox.showinfo("提示", "请先选择要删除的报告")
            return

        for item in selection:
            filename = self.tree.item(item, "values")[0]
            if filename in self.reports:
                del self.reports[filename]
            self.tree.delete(item)

        self._log(f"已删除 {len(selection)} 个报告", "warning")

    def _clear_cache(self):
        """清空评分缓存和导出文件"""
        # 1. 清空评分缓存
        if self.grader:
            self.grader.clear_cache()
            self._log("评分缓存已清空", "success")
        else:
            self._log("评分器未初始化，无法清空缓存", "warning")

        # 2. 删除 data/output/ 目录下的导出文件
        output_deleted = 0
        if OUTPUT_DIR.exists():
            for f in OUTPUT_DIR.iterdir():
                if f.is_file():
                    try:
                        f.unlink()
                        output_deleted += 1
                    except Exception as e:
                        self._log(f"删除文件失败 {f.name}: {e}", "warning")
            self._log(f"已删除 output/ 目录 {output_deleted} 个导出文件", "success")

        # 3. 清空当前会话中的报告列表
        self.reports.clear()
        for item in self.tree.get_children():
            self.tree.delete(item)
        self._log("报告列表已清空", "success")

    def _start_grading(self):
        """开始批阅（集成风格管理）"""
        self._log_behavior("start_grading", {"report_count": len(self.reports)})
        if self.grader is None or not self.grader_available:
            messagebox.showerror("错误", "Kimi API未配置，无法进行智能评分！")
            return

        pending = [f for f, d in self.reports.items() if d["status"] == "待处理"]
        if not pending:
            messagebox.showinfo("提示", "没有待处理的报告！")
            return

        self._active_grading_filenames = list(pending)
        self._current_grading_filename = None

        # 重置取消标志
        self._grading_cancelled = False
        if self.grader:
            self.grader.reset_cancel()

        style_info = self.style_manager.get_style_info()
        self._log(f"使用批阅风格: {style_info['name']} (严格度: {style_info.get('strictness', 1.0)})")

        cache_stats = self.grader.get_cache_stats()
        self._log(f"评分缓存: {cache_stats['total_cached']} 条记录")

        self.progress["maximum"] = len(pending)
        self.progress["value"] = 0
        self.status_label.configure(text=f"正在批阅 0/{len(pending)}...")

        # 启用停止按钮
        if hasattr(self, 'btn_stop'):
            self.btn_stop.configure(state=tk.NORMAL)

        # 预热评分队列
        if self.grader:
            if not self.grader.queue._running:
                self.grader.queue.start()
            queue_status = self.grader.get_queue_status()
            self._log(f"评分队列状态: 运行中={queue_status['running']}, "
                      f"队列长度={queue_status['queue_size']}, "
                      f"已处理={queue_status['processed']}")

        self._grading_thread = threading.Thread(target=self._grading_worker, args=(pending, False))
        self._grading_thread.daemon = True
        self._grading_thread.start()

    def _grading_worker(self, filenames: list, force_refresh: bool = False):
        """批阅工作线程（集成风格和质量检测）。force_refresh=True 时跳过评分缓存。"""
        total = len(filenames)

        # 启动评分队列
        if self.grader and not self.grader.queue._running:
            self.grader.queue.start()
            self.root.after(0, lambda: self._log("评分队列已启动（串行处理 + 智能退避）", "info"))
        
        # 显示速率限制器状态
        if self.grader:
            rate_status = self.grader.get_rate_limiter_status()
            self.root.after(0, lambda rs=rate_status: self._log(
                f"速率控制: 基础间隔 {rs['min_delay']}s, 最大间隔 {rs['max_delay']}s, "
                f"当前间隔 {rs['current_delay']}s", "info"))

        for i, filename in enumerate(filenames):
            if self._grading_cancelled:
                remaining = filenames[i:]
                self.root.after(0, lambda rest=remaining: self._mark_reports_for_continue(rest))
                break

            self.root.after(0, lambda f=filename, idx=i+1, t=total: self._update_progress(f, idx, t))

            try:
                report = self.reports[filename]
                ext = report["path"].suffix.lower()

                if ext == ".pdf":
                    self.root.after(0, lambda f=filename: self._log(f"正在处理PDF: {f}"))
                    pages = self.ocr.process_pdf(report["path"])
                    text = "\n\n".join(pages)
                elif ext == ".docx":
                    # 检查实际格式
                    with open(report["path"], 'rb') as f:
                        header = f.read(4)
                    if header == b'\xd0\xcf\x11\xe0':
                        # 伪装的 .doc
                        self.root.after(0, lambda f=filename: self._log(f"正在处理Word文档(.doc): {f}"))
                        text = self.ocr.process_doc(report["path"])
                    else:
                        self.root.after(0, lambda f=filename: self._log(f"正在处理Word文档: {f}"))
                        text = self.ocr.process_docx(report["path"])
                elif ext == ".doc":
                    self.root.after(0, lambda f=filename: self._log(f"正在处理Word文档(.doc): {f}"))
                    text = self.ocr.process_doc(report["path"])
                elif ext in SUPPORTED_IMAGE_FORMATS:
                    self.root.after(0, lambda f=filename: self._log(f"📄 正在OCR识别: {f}..."))
                    text = self.ocr.recognize(report["path"])
                else:
                    raise ValueError(f"不支持的文件格式: {ext}")

                report["text"] = text

                self.root.after(0, lambda f=filename: self._log(f"📝 正在结构化文本: {f}..."))
                cleaned = self.structurer.clean_text(text)
                sections = self.structurer.extract_sections(cleaned)
                report["sections"] = sections

                # 质量检测
                image_path = report["path"] if ext in SUPPORTED_IMAGE_FORMATS else None
                quality_result = self.quality_checker.full_check(
                    filename=filename,
                    image_path=image_path,
                    text=text,
                    sections=sections
                )
                report["quality"] = quality_result

                self.root.after(0, lambda f=filename, q=quality_result: self._update_quality_display(f, q))

                if self._grading_cancelled:
                    self.root.after(0, lambda f=filename: self._mark_report_for_continue(f))
                    break

                self.root.after(0, lambda f=filename: self._log(f"🤖 正在调用LLM评分: {f}（可能需要10-30秒）..."))
                grader = self.grader
                if grader is None:
                    raise RuntimeError("评分器未初始化")
                
                # 使用当前风格的评分标准
                adjusted_criteria = self.style_manager.get_adjusted_criteria()
                style_hint = self._build_agent_style_hint()
                
                # 确保队列已启动
                if not grader.queue._running:
                    grader.queue.start()
                
                # 使用队列异步评分；继续批阅时跳过缓存，避免复用停止/失败时的默认50分。
                result_dict = grader.grade_with_style_to_dict(
                    sections, style_hint, adjusted_criteria, use_cache=not force_refresh
                )

                if self._grading_cancelled:
                    self.root.after(0, lambda f=filename: self._mark_report_for_continue(f))
                    break
                
                # 检查是否是默认错误结果
                if result_dict.get("weaknesses") and "API服务器过载" in str(result_dict.get("weaknesses", [])):
                    report["status"] = "API过载，已重试"
                else:
                    report["status"] = "已完成"
                
                report["score"] = result_dict

                # 保存到数据库（非取消状态，始终插入新记录）
                if not self._grading_cancelled:
                    user = self.auth.get_current_user()
                    if user:
                        record_id = self.db.save_grading_record(
                            user_id=user.id,
                            filename=filename,
                            file_path=str(report["path"]),
                            score_data=result_dict,
                            ocr_text=text,
                            sections=sections,
                            quality_score=quality_result.overall_score if quality_result else None,
                            style_key=self.style_manager.get_current_style(),
                            style_name=self.style_manager.get_style_info()["name"],
                            is_aborted=False
                        )
                        if record_id > 0:
                            self.root.after(0, lambda: self._log(f"  ✅ 已保存到数据库 (ID: {record_id})", "success"))
                        else:
                            self.root.after(0, lambda: self._log("  ⚠️ 保存数据库记录失败", "warning"))

                # 添加到仪表盘历史
                self.dashboard.add_record(filename, result_dict)

                self.root.after(0, lambda f=filename, r=result_dict: self._update_result(f, r))

            except Exception as e:
                if self._grading_cancelled:
                    self.root.after(0, lambda f=filename: self._mark_report_for_continue(f))
                    break
                report["status"] = "失败"
                self.root.after(0, lambda f=filename, e=str(e): self._log(f"批阅失败 {f}: {e}", "error"))

        self.root.after(0, lambda: self._grading_done(self._grading_cancelled))

    def _update_quality_display(self, filename: str, result):
        """更新质量显示"""
        quality_text = "✅" if result.is_acceptable else "⚠️"
        for item in self.tree.get_children():
            if self.tree.item(item, "values")[0] == filename:
                values = list(self.tree.item(item, "values"))
                values[3] = f"{quality_text} {result.overall_score}"
                self.tree.item(item, values=tuple(values))
                break

    def _update_progress(self, filename: str, current: int, total: int):
        """更新进度"""
        self._current_grading_filename = filename
        report = self.reports.get(filename)
        if report and report.get("status") != "已完成":
            report["status"] = "正在处理"
            for item in self.tree.get_children():
                if self.tree.item(item, "values")[0] == filename:
                    values = list(self.tree.item(item, "values"))
                    values[1] = "正在处理"
                    self.tree.item(item, values=tuple(values))
                    break
        self.progress["value"] = current
        self.status_label.configure(text=f"正在批阅 {current}/{total}: {filename}")
        self._log(f"处理中 ({current}/{total}): {filename}")

    def _update_result(self, filename: str, result: dict):
        """更新单个结果"""
        for item in self.tree.get_children():
            if self.tree.item(item, "values")[0] == filename:
                self.tree.item(item, values=(filename, "已完成", f"{result['total_score']}/{result['total_max']}", 
                                              self.tree.item(item, "values")[3]))
                break
        self._log(f"✅ {filename} 评分完成: {result['total_score']}/{result['total_max']}", "success")

    def _grading_done(self, cancelled=False):
        """批阅完成或取消"""
        if cancelled:
            self.status_label.configure(text="批阅已取消")
            self._log("批阅已取消！", "warning")
            # 停止评分队列
            if self.grader:
                self.grader.stop_queue()
        else:
            self.status_label.configure(text="批阅完成")
            self.progress["value"] = self.progress["maximum"]
            self._log("✅ 所有报告批阅完成！", "success")
            self._log_progress_alert_if_needed()

        # 禁用停止按钮
        if hasattr(self, 'btn_stop'):
            self.btn_stop.configure(state=tk.DISABLED)

        if self.grader:
            stats = self.grader.get_cache_stats()
            self._log(f"💾 当前缓存: {stats['total_cached']} 条记录")

        if not cancelled:
            self._active_grading_filenames = []
            self._current_grading_filename = None
            messagebox.showinfo("完成", "批阅完成！")
        # 显示队列最终状态
        if self.grader:
            queue_status = self.grader.get_queue_status()
            self._log(f"📊 队列状态: 已处理 {queue_status['processed']}/{queue_status['total']} 个请求", "info")
            rate_status = self.grader.get_rate_limiter_status()
            self._log(f"⏱️ 最终速率间隔: {rate_status['current_delay']}s", "info")

    def _stop_grading(self):
        """停止批阅，并把未完成的报告标记为“待继续”。"""
        if self._grading_thread is None or not self._grading_thread.is_alive():
            self._log("当前没有正在进行的批阅任务", "info")
            return

        self._grading_cancelled = True
        self._log("正在停止批阅...", "warning")

        # 停止评分队列，清空待处理请求
        if self.grader:
            self.grader.stop_queue()
            self._log("评分队列已停止", "warning")

        unfinished = []
        active_names = list(getattr(self, "_active_grading_filenames", []) or self.reports.keys())
        for filename in active_names:
            report = self.reports.get(filename)
            if not report:
                continue
            if report.get("status") != "已完成" or self._is_default_placeholder_score(report.get("score")):
                unfinished.append(filename)
        self._mark_reports_for_continue(unfinished)

        self.status_label.configure(text="批阅已取消，可点击“继续批阅”恢复")
        if hasattr(self, 'btn_stop'):
            self.btn_stop.configure(state=tk.DISABLED)
        self._log(f"批阅已停止，{len(unfinished)} 份未完成报告已标记为待继续", "warning")

    def _is_default_placeholder_score(self, score: Optional[dict]) -> bool:
        """识别停止/失败时生成的默认50分占位结果，避免误当作真实评分。"""
        if not isinstance(score, dict):
            return False
        text_parts = [
            str(score.get("overall_comment", "")),
            str(score.get("overall_suggestions", "")),
            " ".join(map(str, score.get("weaknesses", []) or [])),
            " ".join(map(str, score.get("strengths", []) or [])),
        ]
        marker_text = "\n".join(text_parts)
        default_markers = [
            "评分过程出错", "默认分数", "评分失败", "请检查API配置或重试", "操作已取消", "API服务器过载"
        ]
        if any(marker in marker_text for marker in default_markers):
            return True
        try:
            total = float(score.get("total_score", -1))
            total_max = float(score.get("total_max", 100) or 100)
            pct = float(score.get("percentage", -1))
            strengths = score.get("strengths", []) or []
            return abs(total - total_max / 2) < 1e-6 and abs(pct - 50.0) < 1e-6 and (not strengths or strengths == ["无"])
        except Exception:
            return False

    def _get_continue_candidates(self) -> list:
        """获取适合由“继续批阅”批量恢复的报告。"""
        candidates = []
        retry_statuses = {"待处理", "待继续", "正在处理", "失败", "API过载，已重试", "已取消"}
        for filename, report in self.reports.items():
            status = report.get("status")
            if status in retry_statuses or self._is_default_placeholder_score(report.get("score")):
                candidates.append(filename)
        return candidates

    def _set_tree_report_row(self, filename: str, status: str, score_text: Optional[str] = None, quality_text: Optional[str] = None):
        """安全更新主列表中的某一行。"""
        for item in self.tree.get_children():
            values = list(self.tree.item(item, "values"))
            if values and values[0] == filename:
                values[1] = status
                if score_text is not None:
                    values[2] = score_text
                if quality_text is not None:
                    values[3] = quality_text
                self.tree.item(item, values=tuple(values))
                break

    def _mark_report_for_continue(self, filename: str, clear_score: bool = True):
        """把报告标记为待继续，清除停止产生的默认占位分。"""
        report = self.reports.get(filename)
        if not report:
            return
        report["status"] = "待继续"
        if clear_score:
            report["score"] = None
        quality = report.get("quality")
        quality_text = "-"
        if quality:
            quality_text = f"{'✅' if quality.is_acceptable else '⚠️'} {quality.overall_score}"
        self._set_tree_report_row(filename, "待继续", "-", quality_text)

    def _mark_reports_for_continue(self, filenames: list):
        for filename in filenames:
            self._mark_report_for_continue(filename)

    def _reset_report_for_continue(self, filename: str):
        """继续批阅前重置内存状态，确保重新调用API而不是沿用默认50分。"""
        report = self.reports.get(filename)
        if not report:
            return
        report["status"] = "待处理"
        report["score"] = None
        report["text"] = ""
        report["sections"] = {}
        report["quality"] = None
        self._set_tree_report_row(filename, "待处理", "-", "-")

    def _continue_grading(self):
        """批量继续批阅停止后留下的待继续/默认50分/失败报告。"""
        self._log_behavior("continue_grading", {"report_count": len(self.reports)})
        if self.grader is None or not self.grader_available:
            messagebox.showerror("错误", "Kimi API未配置，无法继续批阅！")
            return
        if self._grading_thread is not None and self._grading_thread.is_alive():
            messagebox.showinfo("提示", "当前已有批阅任务正在进行，请先停止或等待完成。")
            return

        candidates = self._get_continue_candidates()
        if not candidates:
            messagebox.showinfo("提示", "没有发现需要继续批阅的报告。")
            return

        preview = "\n".join(f"• {name}" for name in candidates[:12])
        if len(candidates) > 12:
            preview += f"\n……等共 {len(candidates)} 份"
        msg = (
            f"将继续批阅 {len(candidates)} 份报告。\n\n"
            "系统会自动清除停止/失败时产生的默认50分占位，并跳过评分缓存重新调用API。\n\n"
            f"待继续列表：\n{preview}\n\n是否开始？"
        )
        if not messagebox.askyesno("继续批阅", msg):
            return

        for filename in candidates:
            self._reset_report_for_continue(filename)

        self._grading_cancelled = False
        self._active_grading_filenames = list(candidates)
        self._current_grading_filename = None
        if self.grader:
            self.grader.reset_cancel()
            if not self.grader.queue._running:
                self.grader.queue.start()

        self.progress["maximum"] = len(candidates)
        self.progress["value"] = 0
        self.status_label.configure(text=f"准备继续批阅 0/{len(candidates)}...")
        if hasattr(self, 'btn_stop'):
            self.btn_stop.configure(state=tk.NORMAL)

        self._log(f"开始继续批阅 {len(candidates)} 份报告（已跳过缓存）", "info")
        self._grading_thread = threading.Thread(target=self._grading_worker, args=(candidates, True))
        self._grading_thread.daemon = True
        self._grading_thread.start()

    def _regrade_selected(self):
        """重新批阅选中的报告（弹出选项对话框）"""
        selection = self.tree.selection()
        if not selection:
            messagebox.showinfo("提示", "请先选择要重新批阅的报告")
            return

        filename = self.tree.item(selection[0], "values")[0]
        report = self.reports.get(filename)
        if not report:
            messagebox.showerror("错误", "报告数据不存在")
            return

        # 检查是否有正在进行的批阅任务
        if self._grading_thread is not None and self._grading_thread.is_alive():
            if not messagebox.askyesno("确认", "当前有批阅任务正在进行，是否停止当前任务并重新批阅？"):
                return
            self._stop_grading()
            import time
            time.sleep(0.5)

        # 弹出选项对话框
        dialog = tk.Toplevel(self.root)
        dialog.title("重新批阅选项")
        self._setup_dialog_window(dialog, width=520, height=380, min_width=460, min_height=320)
    
        theme = self._get_theme()
        dialog.configure(bg=theme["bg"])

        frame = ttk.Frame(dialog, padding="20")
        frame.pack(fill=tk.BOTH, expand=True)

        ttk.Label(frame, text=f"重新批阅: {filename}", font=("微软雅黑", 12, "bold")).pack(pady=(0, 15))

        choice_var = tk.StringVar(value="new")

        ttk.Radiobutton(frame, text="生成新记录（在历史记录中新增一条）", 
                        variable=choice_var, value="new").pack(anchor=tk.W, pady=5)
        ttk.Radiobutton(frame, text="覆盖历史记录（选择已有记录ID进行覆盖）", 
                        variable=choice_var, value="overwrite").pack(anchor=tk.W, pady=5)

        result: dict[str, Any] = {"choice": None, "record_id": None}

        def on_confirm():
            choice = choice_var.get()
            result["choice"] = choice
            dialog.destroy()

            if choice == "overwrite":
                # 弹出选择记录ID的对话框
                self._show_overwrite_dialog(filename, report)
            else:
                # 生成新记录
                self._do_regrade(filename, report, overwrite_id=None)

        def on_cancel():
            dialog.destroy()

        btn_frame = ttk.Frame(frame)
        btn_frame.pack(pady=(20, 0))
        ttk.Button(btn_frame, text="确定", command=on_confirm).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="取消", command=on_cancel).pack(side=tk.LEFT, padx=5)

    def _show_overwrite_dialog(self, filename: str, report: dict):
        """显示覆盖历史记录的选择对话框"""
        user = self.auth.get_current_user()
        if not user:
            messagebox.showwarning("提示", "请先登录")
            return

        # 获取该文件名的所有历史记录（复用 history_mgr 的 display_id 计算）
        all_records = self.history_mgr.get_user_history(user.id, page=1, page_size=100)["records"]
        # 过滤出文件名匹配的记录（保持原有的 display_id）
        matching_records = [r for r in all_records if r.filename == filename]

        if not matching_records:
            messagebox.showinfo("提示", f"历史记录中没有名为 '{filename}' 的记录，将生成新记录")
            self._do_regrade(filename, report, overwrite_id=None)
            return

        dialog = tk.Toplevel(self.root)
        dialog.title("选择要覆盖的记录")
        self._setup_dialog_window(dialog, width=800, height=620, min_width=650, min_height=460)

        frame = ttk.Frame(dialog, padding="15")
        frame.pack(fill=tk.BOTH, expand=True)

        ttk.Label(frame, text=f"文件: {filename}", font=("微软雅黑", 11, "bold")).pack(anchor=tk.W, pady=(0, 10))
        ttk.Label(frame, text="请选择要覆盖的历史记录:", font=("微软雅黑", 10)).pack(anchor=tk.W, pady=(0, 5))

        # 树形列表显示历史记录
        columns = ("id", "score", "style", "date")
        tree_container = ttk.Frame(frame)
        tree_container.pack(fill=tk.BOTH, expand=True, pady=5)
        tree = ttk.Treeview(tree_container, columns=columns, show="headings", height=10)
        tree.heading("id", text="记录ID")
        tree.heading("score", text="分数")
        tree.heading("style", text="风格")
        tree.heading("date", text="时间")
        tree.column("id", width=80, anchor="center")
        tree.column("score", width=100, anchor="center")
        tree.column("style", width=100, anchor="center")
        tree.column("date", width=150, anchor="center")

        # 直接使用记录自带的 display_id，不要重新计算
        for r in matching_records:
            tree.insert("", tk.END, values=(
                r.display_id,
                f"{r.total_score}/{r.total_max} ({r.percentage}%)",
                r.style_name,
                r.created_at.strftime("%Y-%m-%d %H:%M:%S")
            ))

        tree_scroll_y = ttk.Scrollbar(tree_container, orient="vertical", command=tree.yview)
        tree_scroll_x = ttk.Scrollbar(tree_container, orient="horizontal", command=tree.xview)
        tree.configure(yscrollcommand=tree_scroll_y.set, xscrollcommand=tree_scroll_x.set)
        tree.grid(row=0, column=0, sticky="nsew")
        tree_scroll_y.grid(row=0, column=1, sticky="ns")
        tree_scroll_x.grid(row=1, column=0, sticky="ew")
        tree_container.columnconfigure(0, weight=1)
        tree_container.rowconfigure(0, weight=1)

        def on_confirm():
            selection = tree.selection()
            if not selection:
                messagebox.showwarning("提示", "请先选择一条记录")
                return
            display_id = int(tree.item(selection[0], "values")[0])
            # 通过 display_id 找到对应的真实记录
            target_record = None
            for r in matching_records:
                if r.display_id == display_id:
                    target_record = r
                    break
        
            if not target_record:
                messagebox.showerror("错误", "未找到对应的记录")
                return
            
            dialog.destroy()
            self._do_regrade(filename, report, overwrite_id=target_record.id)

        def on_cancel():
            dialog.destroy()

        btn_frame = ttk.Frame(frame)
        btn_frame.pack(pady=(10, 0))
        ttk.Button(btn_frame, text="覆盖选中记录", command=on_confirm).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="取消", command=on_cancel).pack(side=tk.LEFT, padx=5)

    def _do_regrade(self, filename: str, report: dict, overwrite_id: Optional[int] = None):
        """执行重新批阅"""
        # 重置报告状态
        report["status"] = "待处理"
        report["score"] = None
        report["text"] = ""
        report["sections"] = {}
        report["quality"] = None

        #更新树形列表
        for item in self.tree.get_children():
            if self.tree.item(item, "values")[0] == filename:
                self.tree.item(item, values=(filename, "待处理", "-", "-"))
                break

        # 清除右侧显示
        self.score_label.configure(text="未评分")
        self.dim_text.delete(1.0, tk.END)
        self.dim_text.insert(tk.END, "暂无评分结果")
        self.comment_text.delete(1.0, tk.END)
        self.raw_text.delete(1.0, tk.END)
        self.quality_text.configure(state=tk.NORMAL)
        self.quality_text.delete(1.0, tk.END)
        self.quality_text.configure(state=tk.DISABLED)

        # 清除缓存确保重新调用API
        if self.grader:
            self.grader.clear_cache()
            self._log("已清除评分缓存，确保重新调用API", "info")

        self._log(f"已重置报告 '{filename}' 状态，准备重新批阅...", "info")

        # 立即开始批阅
        self._grading_cancelled = False
        if self.grader:
            self.grader.reset_cancel()
        self.progress["maximum"] = 1
        self.progress["value"] = 0
        self.status_label.configure(text=f"正在重新批阅: {filename}")

        if hasattr(self, 'btn_stop'):
            self.btn_stop.configure(state=tk.NORMAL)

        if self.grader:
            if not self.grader.queue._running:
                self.grader.queue.start()

        self._grading_thread = threading.Thread(
            target=self._regrade_worker_with_option, 
            args=(filename, overwrite_id)
        )
        self._grading_thread.daemon = True
        self._grading_thread.start()

    def _regrade_worker_with_option(self, filename: str, overwrite_id: Optional[int] = None):
        """重新批阅工作线程（支持覆盖或新建）"""
        try:
            report = self.reports[filename]
            ext = report["path"].suffix.lower()

            if self._grading_cancelled:
                self.root.after(0, lambda: self._log("重新批阅已取消", "warning"))
                self.root.after(0, lambda: self._grading_done(True))
                return

            self.root.after(0, lambda f=filename: self._log(f"正在重新批阅: {f}"))

            if ext == ".pdf":
                self.root.after(0, lambda f=filename: self._log(f"正在处理PDF: {f}"))
                pages = self.ocr.process_pdf(report["path"])
                text = "\n\n".join(pages)
            elif ext == ".docx":
                    # 检查实际格式
                    with open(report["path"], 'rb') as f:
                        header = f.read(4)
                    if header == b'\xd0\xcf\x11\xe0':
                        # 伪装的 .doc
                        self.root.after(0, lambda f=filename: self._log(f"正在处理Word文档(.doc): {f}"))
                        text = self.ocr.process_doc(report["path"])
                    else:
                        self.root.after(0, lambda f=filename: self._log(f"正在处理Word文档: {f}"))
                        text = self.ocr.process_docx(report["path"])
            elif ext == ".doc":
                    self.root.after(0, lambda f=filename: self._log(f"正在处理Word文档(.doc): {f}"))
                    text = self.ocr.process_doc(report["path"])
            elif ext in SUPPORTED_IMAGE_FORMATS:
                self.root.after(0, lambda f=filename: self._log(f"📄 正在OCR识别: {f}..."))
                text = self.ocr.recognize(report["path"])
            else:
                raise ValueError(f"不支持的文件格式: {ext}")

            report["text"] = text

            self.root.after(0, lambda f=filename: self._log(f"正在结构化: {f}"))
            cleaned = self.structurer.clean_text(text)
            sections = self.structurer.extract_sections(cleaned)
            report["sections"] = sections

            # 质量检测
            image_path = report["path"] if ext in SUPPORTED_IMAGE_FORMATS else None
            quality_result = self.quality_checker.full_check(
                filename=filename,
                image_path=image_path,
                text=text,
                sections=sections
            )
            report["quality"] = quality_result

            self.root.after(0, lambda f=filename, q=quality_result: self._update_quality_display(f, q))

            self.root.after(0, lambda f=filename: self._log(f"正在LLM评分: {f}"))
            grader = self.grader
            if grader is None:
                raise RuntimeError("评分器未初始化")

            adjusted_criteria = self.style_manager.get_adjusted_criteria()
            style_hint = self._build_agent_style_hint()

            if not grader.queue._running:
                grader.queue.start()

            result_dict = grader.grade_with_style_to_dict(sections, style_hint, adjusted_criteria, use_cache=False)

            if result_dict.get("weaknesses") and "API服务器过载" in str(result_dict.get("weaknesses", [])):
                report["status"] = "API过载，已重试"
            else:
                report["status"] = "已完成"

            report["score"] = result_dict

            # 保存到数据库（覆盖或新建）
            if not self._grading_cancelled:
                user = self.auth.get_current_user()
                if user:
                    if overwrite_id is not None:
                        # 覆盖原有记录
                        success = self.db.update_grading_record(
                            record_id=overwrite_id,
                            score_data=result_dict,
                            ocr_text=report.get("text", ""),
                            sections=report.get("sections", {}),
                            quality_score=report["quality"].overall_score if report.get("quality") else None,
                            style_key=self.style_manager.get_current_style(),
                            style_name=self.style_manager.get_style_info()["name"]
                        )
                        if success:
                            self.root.after(0, lambda: self._log(f"  ✅ 已覆盖数据库记录 (ID: {overwrite_id})", "success"))
                        else:
                            self.root.after(0, lambda: self._log("  ⚠️ 覆盖数据库记录失败，已生成新记录", "warning"))
                            # 覆盖失败则生成新记录
                            self.db.save_grading_record(
                                user_id=user.id,
                                filename=filename,
                                file_path=str(report["path"]),
                                score_data=result_dict,
                                ocr_text=report.get("text", ""),
                                sections=report.get("sections", {}),
                                quality_score=report["quality"].overall_score if report.get("quality") else None,
                                style_key=self.style_manager.get_current_style(),
                                style_name=self.style_manager.get_style_info()["name"],
                                is_aborted=False
                            )
                    else:
                        # 生成新记录
                        record_id = self.db.save_grading_record(
                            user_id=user.id,
                            filename=filename,
                            file_path=str(report["path"]),
                            score_data=result_dict,
                            ocr_text=report.get("text", ""),
                            sections=report.get("sections", {}),
                            quality_score=report["quality"].overall_score if report.get("quality") else None,
                            style_key=self.style_manager.get_current_style(),
                            style_name=self.style_manager.get_style_info()["name"],
                            is_aborted=False
                        )
                        if record_id > 0:
                            self.root.after(0, lambda: self._log(f"  ✅ 已生成新数据库记录 (ID: {record_id})", "success"))
                        else:
                            self.root.after(0, lambda: self._log("  ⚠️ 保存数据库记录失败", "warning"))

            # 添加到仪表盘历史
            self.dashboard.add_record(filename, result_dict)

            self.root.after(0, lambda f=filename, r=result_dict: self._update_result(f, r))
            self.root.after(0, lambda: self._log(f"重新批阅完成: {filename}", "success"))

        except Exception as e:
            report["status"] = "失败"
            self.root.after(0, lambda f=filename, e=str(e): self._log(f"重新批阅失败 {f}: {e}", "error"))

        self.root.after(0, lambda: self._grading_done(self._grading_cancelled))

    def _on_select(self, event):
        """选择报告时显示详情"""
        selection = self.tree.selection()
        if not selection:
            return

        filename = self.tree.item(selection[0], "values")[0]
        report = self.reports.get(filename)

        if not report:
            return

        self.raw_text.delete(1.0, tk.END)
        self.raw_text.insert(tk.END, report.get("text", "无原文"))

        # 显示质量检测结果
        quality = report.get("quality")
        if quality:
            self._display_quality_result(quality)

        score = report.get("score")
        if score:
            self.score_label.configure(
                text=f"{score['total_score']} / {score['total_max']}\n({score['percentage']}%)"
            )

            self.dim_text.delete(1.0, tk.END)
            for name, dim in score["dimensions"].items():
                self.dim_text.insert(tk.END, f"【{name}】\n")
                self.dim_text.insert(tk.END, f"  得分: {dim['score']} / {dim['max_score']}\n")
                self.dim_text.insert(tk.END, f"  评语: {dim['comment']}\n")
                self.dim_text.insert(tk.END, f"  建议: {dim['suggestions']}\n\n")

            self.comment_text.delete(1.0, tk.END)
            self.comment_text.insert(tk.END, f"💬 总体评语:\n{score['overall_comment']}\n\n")
            self.comment_text.insert(tk.END, f"💡 总体建议:\n{score['overall_suggestions']}\n\n")
            self.comment_text.insert(tk.END, "✅ 优点:\n")
            for s in score["strengths"]:
                self.comment_text.insert(tk.END, f"  - {s}\n")
            self.comment_text.insert(tk.END, "\n⚠️ 不足:\n")
            for w in score["weaknesses"]:
                self.comment_text.insert(tk.END, f"  - {w}\n")
        else:
            self.score_label.configure(text="未评分")
            self.dim_text.delete(1.0, tk.END)
            self.dim_text.insert(tk.END, "暂无评分结果")
            self.comment_text.delete(1.0, tk.END)

    def _show_history_dialog(self):
        """显示历史记录对话框（增强版：排序、搜索、删除、新增、改动、发送邮件）"""
        self._log_behavior("history")
        user = self.auth.get_current_user()
        if not user:
            messagebox.showwarning("提示", "请先登录")
            return

        dialog = tk.Toplevel(self.root)
        dialog.title("历史记录")
        dialog.geometry("1900x900")
        dialog.transient(self.root)

        # 主框架
        main_frame = ttk.Frame(dialog, padding="10")
        main_frame.pack(fill=tk.BOTH, expand=True)
        main_frame.columnconfigure(0, weight=1)
        main_frame.rowconfigure(2, weight=1)

        # === 顶部工具栏（第一部分：不依赖 tree 的控件）===
        toolbar = ttk.Frame(main_frame)
        toolbar.grid(row=0, column=0, sticky="ew", pady=(0, 10))

        # 搜索框
        ttk.Label(toolbar, text="搜索文件名:").pack(side=tk.LEFT, padx=(0, 5))
        search_entry = ttk.Entry(toolbar, textvariable=self.history_search_var, width=25)
        search_entry.pack(side=tk.LEFT, padx=(0, 5))
        # 查找、清空、刷新按钮先占位，tree创建后再配置command
        btn_search = ttk.Button(toolbar, text="🔍 查找")
        btn_search.pack(side=tk.LEFT, padx=5)
        btn_clear = ttk.Button(toolbar, text="清空")
        btn_clear.pack(side=tk.LEFT, padx=5)
        btn_refresh = ttk.Button(toolbar, text="🔄 刷新")
        btn_refresh.pack(side=tk.LEFT, padx=5)

        # 排序选项
        ttk.Label(toolbar, text="排序:").pack(side=tk.LEFT, padx=(20, 5))
        sort_combo = ttk.Combobox(toolbar, textvariable=self.history_sort_by, 
                                   values=["按ID"],
                                   width=12, state="readonly")
        sort_combo.pack(side=tk.LEFT, padx=5)
        
        ttk.Radiobutton(toolbar, text="倒序", variable=self.history_sort_order, value="desc").pack(side=tk.LEFT, padx=2)
        ttk.Radiobutton(toolbar, text="正序", variable=self.history_sort_order, value="asc").pack(side=tk.LEFT, padx=2)

        # === 提示标签 ===
        tip_label = ttk.Label(
            main_frame, 
            text="💡 提示: 双击表格中的记录可查看完整详情；勾选左侧复选框可多选记录",
            font=("微软雅黑", 10),
            foreground="gray"
        )
        tip_label.grid(row=1, column=0, sticky=tk.W, pady=(0, 8))

        # === 树形列表 ===
        tree_frame = ttk.Frame(main_frame)
        tree_frame.grid(row=2, column=0, sticky="nsew")
        tree_frame.columnconfigure(0, weight=1)
        tree_frame.rowconfigure(0, weight=1)

        columns = ("select", "display_id", "filename", "score", "style", "date", "comment", "suggestions", "ocr_text", "modified")
        self.history_tree = tree = ttk.Treeview(tree_frame, columns=columns, show="headings", height=15)
        tree.heading("select", text="☐")
        tree.heading("display_id", text="ID")
        tree.heading("filename", text="文件名")
        tree.heading("score", text="分数")
        tree.heading("style", text="风格")
        tree.heading("date", text="时间")
        tree.heading("comment", text="评语")
        tree.heading("suggestions", text="建议")
        tree.heading("ocr_text", text="OCR原文")
        tree.heading("modified", text="改动")
        tree.column("select", width=40, anchor="center")
        tree.column("display_id", width=50, anchor="center")
        tree.column("filename", width=120, anchor="w")
        tree.column("score", width=70, anchor="center")
        tree.column("style", width=70, anchor="center")
        tree.column("date", width=120, anchor="center")
        tree.column("comment", width=100, anchor="w")
        tree.column("suggestions", width=100, anchor="w")
        tree.column("ocr_text", width=100, anchor="w")
        tree.column("modified", width=50, anchor="center")

        # 滚动条
        vsb = ttk.Scrollbar(tree_frame, orient="vertical", command=tree.yview)
        hsb = ttk.Scrollbar(tree_frame, orient="horizontal", command=tree.xview)
        tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)

        tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")

        # 存储当前所有记录供后续操作使用
        self._current_history_records = []

        # 初始加载
        self._refresh_history_tree(tree, user.id)

        # 双击事件
        tree.bind("<Double-1>", lambda e: self._on_history_double_click(e, tree, user.id))

        # 点击复选框
        tree.bind("<Button-1>", lambda e: self._on_tree_checkbox_click(e, tree))

        # === 工具栏（第二部分：依赖 tree 的按钮，在 tree 创建后添加）===
        # 配置查找、清空、刷新按钮的 command
        btn_search.configure(command=lambda: self._refresh_history_tree(self.history_tree, user.id))
        btn_clear.configure(command=lambda: [self.history_search_var.set(""), self._refresh_history_tree(self.history_tree, user.id)])
        btn_refresh.configure(command=lambda: self._refresh_history_tree(self.history_tree, user.id))
        sort_combo.bind("<<ComboboxSelected>>", lambda e: self._refresh_history_tree(self.history_tree, user.id))

        # 记录操作分组
        ops_frame = ttk.LabelFrame(toolbar, text="记录", padding="2")
        ops_frame.pack(side=tk.LEFT, padx=(15, 5))
        ttk.Button(ops_frame, text="➕ 新增", width=8, command=lambda: self._show_add_record_dialog(self.history_tree, user.id)).pack(side=tk.LEFT, padx=2)
        ttk.Button(ops_frame, text="✏️ 改动", width=8, command=lambda: self._show_edit_record_dialog(self.history_tree, user.id)).pack(side=tk.LEFT, padx=2)
        ttk.Button(ops_frame, text="🗑️ 删除", width=10, command=lambda: self._delete_history_records(self.history_tree, user.id)).pack(side=tk.LEFT, padx=2)

        # 导出菜单（下拉）
        export_frame = ttk.LabelFrame(toolbar, text="导出", padding="2")
        export_frame.pack(side=tk.LEFT, padx=5)
        
        export_mb = ttk.Menubutton(export_frame, text="📦 导出选中", width=12)
        export_mb.pack(side=tk.LEFT, padx=2)
        export_menu = tk.Menu(export_mb, tearoff=0)
        export_mb.configure(menu=export_menu)
        
        export_menu.add_command(label="📊 Excel (.xlsx)", command=lambda: self._export_history_dialog_from_tree(self.history_tree, user.id, "excel"))
        export_menu.add_command(label="📄 PDF (.pdf)", command=lambda: self._export_history_dialog_from_tree(self.history_tree, user.id, "pdf"))
        export_menu.add_command(label="📽 PPT (.pptx)", command=lambda: self._export_history_dialog_from_tree(self.history_tree, user.id, "ppt"))
        export_menu.add_command(label="📝 Word (.docx)", command=lambda: self._export_history_dialog_from_tree(self.history_tree, user.id, "docx"))
        export_menu.add_separator()
        export_menu.add_command(label="📦 导出选中 + 发送邮件", command=lambda: self._export_selected_history_with_email(self.history_tree, user.id))

        # 邮件分组
        mail_frame = ttk.LabelFrame(toolbar, text="邮件", padding="2")
        mail_frame.pack(side=tk.LEFT, padx=5)
        ttk.Button(mail_frame, text="📧 发送邮件", width=10, command=lambda: self._send_history_email(self.history_tree, user.id)).pack(side=tk.LEFT, padx=2)

        # 全选
        ttk.Checkbutton(toolbar, text="全选", variable=self.history_select_all_var, 
                       command=lambda: self._toggle_select_all(self.history_tree)).pack(side=tk.RIGHT, padx=5)

        # 关闭按钮
        btn_frame = ttk.Frame(main_frame)
        btn_frame.grid(row=3, column=0, pady=(10, 0))
        ttk.Button(btn_frame, text="✗ 关闭", command=dialog.destroy).pack()

    def _export_selected_history_with_email(self, tree, user_id):
        """导出选中的历史记录（支持直接导出或发送邮件）"""
        selected_ids = self._get_selected_record_ids(tree)
        if not selected_ids:
            messagebox.showinfo("提示", "请先勾选要导出的记录")
            return

        # 获取选中的记录
        selected_records = [r for r in self._current_history_records if r.id in selected_ids]
        if not selected_records:
            messagebox.showwarning("错误", "未找到选中的记录")
            return

        # 弹出选项对话框
        dialog = tk.Toplevel(self.root)
        dialog.title("导出选中记录")
        dialog.geometry("500x600")
        dialog.transient(self.root)
        dialog.grab_set()
        dialog.resizable(False, False)

        theme = self._get_theme()
        dialog.configure(bg=theme["bg"])

        frame = ttk.Frame(dialog, padding="20")
        frame.pack(fill=tk.BOTH, expand=True)

        ttk.Label(frame, text=f"已选中 {len(selected_records)} 条记录", 
                  font=("微软雅黑", 12, "bold")).pack(anchor=tk.W, pady=(0, 15))

        # 格式选择
        ttk.Label(frame, text="选择导出格式:", font=("微软雅黑", 10, "bold")).pack(anchor=tk.W, pady=(0, 5))
        format_var = tk.StringVar(value="excel")
        format_frame = ttk.Frame(frame)
        format_frame.pack(fill=tk.X, pady=5)
        for fmt, label in [("excel", "Excel (.xlsx)"), ("pdf", "PDF (.pdf)"), 
                          ("ppt", "PPT (.pptx)"), ("docx", "Word (.docx)")]:
            ttk.Radiobutton(format_frame, text=label, variable=format_var, value=fmt).pack(anchor=tk.W)

        # 操作选择
        ttk.Label(frame, text="选择操作:", font=("微软雅黑", 10, "bold")).pack(anchor=tk.W, pady=(10, 5))
        action_var = tk.StringVar(value="export")
        ttk.Radiobutton(frame, text="直接导出到文件", variable=action_var, value="export").pack(anchor=tk.W)
        ttk.Radiobutton(frame, text="导出并发送邮件", variable=action_var, value="email").pack(anchor=tk.W)

        # 排序选项（仅导出时有效）
        ttk.Label(frame, text="排序:", font=("微软雅黑", 10)).pack(anchor=tk.W, pady=(10, 5))
        sort_var = tk.StringVar(value="desc")
        sort_frame = ttk.Frame(frame)
        sort_frame.pack(fill=tk.X, pady=2)
        ttk.Radiobutton(sort_frame, text="倒序（最新在前）", variable=sort_var, value="desc").pack(side=tk.LEFT, padx=(0, 10))
        ttk.Radiobutton(sort_frame, text="正序（最早在前）", variable=sort_var, value="asc").pack(side=tk.LEFT)

        def on_confirm():
            export_format = format_var.get()
            action = action_var.get()
            sort_order = sort_var.get()
            dialog.destroy()

            # 排序
            records = list(selected_records)
            if sort_order == "asc":
                records = list(reversed(records))

            if action == "export":
                self._do_export_history_records(records, export_format)
            else:
                self._do_export_and_email_history_records(records, export_format)

        btn_frame = ttk.Frame(frame)
        btn_frame.pack(pady=(20, 0))
        ttk.Button(btn_frame, text="确定", command=on_confirm).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="取消", command=dialog.destroy).pack(side=tk.LEFT, padx=5)

    def _do_export_history_records(self, records, export_format: str):
        """直接导出选中的历史记录"""
        format_map = {
            "excel": (".xlsx", "Excel文件", "*.xlsx"),
            "pdf": (".pdf", "PDF文件", "*.pdf"),
            "ppt": (".pptx", "PPT文件", "*.pptx"),
            "docx": (".docx", "Word文件", "*.docx"),
        }
        
        ext, file_type, file_pattern = format_map.get(export_format, (".xlsx", "Excel文件", "*.xlsx"))
        
        user = self.auth.get_current_user()
        filepath = filedialog.asksaveasfilename(
            defaultextension=ext,
            filetypes=[(file_type, file_pattern)],
            initialfile=f"history_selected_{user.username if user else 'unknown'}_{int(datetime.now().timestamp())}{ext}"
        )
        if not filepath:
            return

        try:
            if export_format == "excel":
                self._export_records_to_excel(records, Path(filepath))
            else:
                # 转换为 exporter 需要的格式
                scores = []
                filenames = []
                for r in records:
                    score_data = {
                        "total_score": r.total_score,
                        "total_max": r.total_max,
                        "percentage": r.percentage,
                        "dimensions": r.dimensions,
                        "overall_comment": r.overall_comment,
                        "overall_suggestions": r.overall_suggestions,
                        "strengths": r.strengths,
                        "weaknesses": r.weaknesses,
                    }
                    scores.append(score_data)
                    filenames.append(r.filename)
                
                if export_format == "pdf":
                    self.exporter.export_pdf(scores, filenames, Path(filepath))
                elif export_format == "ppt":
                    self.exporter.export_ppt(scores, filenames, Path(filepath))
                elif export_format == "docx":
                    self.exporter.export_docx(scores, filenames, Path(filepath))
            
            messagebox.showinfo("成功", f"已导出 {len(records)} 条记录到:\n{filepath}")
            self._log(f"历史记录导出成功: {filepath}", "success")
            
        except Exception as e:
            messagebox.showerror("错误", f"导出失败: {e}")
            self._log(f"导出失败: {e}", "error")

    def _do_export_and_email_history_records(self, records, export_format: str):
        """导出并发送邮件"""
        import tempfile
        
        format_map = {
            "excel": ".xlsx",
            "pdf": ".pdf",
            "ppt": ".pptx",
            "docx": ".docx",
        }
        ext = format_map.get(export_format, ".xlsx")
        
        # 创建临时文件
        temp_path = Path(tempfile.gettempdir()) / f"history_selected_{int(datetime.now().timestamp())}{ext}"
        
        try:
            # 先导出到临时文件
            if export_format == "excel":
                self._export_records_to_excel(records, temp_path)
            else:
                scores = []
                filenames = []
                for r in records:
                    score_data = {
                        "total_score": r.total_score,
                        "total_max": r.total_max,
                        "percentage": r.percentage,
                        "dimensions": r.dimensions,
                        "overall_comment": r.overall_comment,
                        "overall_suggestions": r.overall_suggestions,
                        "strengths": r.strengths,
                        "weaknesses": r.weaknesses,
                    }
                    scores.append(score_data)
                    filenames.append(r.filename)
                
                if export_format == "pdf":
                    self.exporter.export_pdf(scores, filenames, temp_path)
                elif export_format == "ppt":
                    self.exporter.export_ppt(scores, filenames, temp_path)
                elif export_format == "docx":
                    self.exporter.export_docx(scores, filenames, temp_path)
            
            # 调用邮件发送对话框
            subject = f"历史记录导出（{len(records)}条记录）"
            self._show_email_dialog_with_attachment(temp_path, subject, records=records)
            
        except Exception as e:
            messagebox.showerror("错误", f"准备附件失败: {e}")
            self._log(f"准备邮件附件失败: {e}", "error")

    def _refresh_history_tree(self, tree, user_id):
        """刷新历史记录树形列表"""
        for item in tree.get_children():
            tree.delete(item)

        sort_by_display = self.history_sort_by.get()
        sort_by = "display_id" if sort_by_display == "按ID" else "created_at"
        sort_order = self.history_sort_order.get()

        result = self.history_mgr.get_user_history(user_id, page=1, page_size=1000)
        all_records = result["records"]

        search_text = self.history_search_var.get().strip().lower()
        if search_text:
            all_records = [r for r in all_records if search_text in r.filename.lower()]

        if sort_by == "display_id":
            all_records.sort(key=lambda r: r.display_id, reverse=(sort_order == "desc"))
        else:
            all_records.sort(key=lambda r: r.created_at, reverse=(sort_order == "desc"))

        self._current_history_records = all_records

        for r in all_records:
            comment = r.overall_comment[:15] + "..." if len(r.overall_comment) > 15 else r.overall_comment
            suggestions = r.overall_suggestions[:15] + "..." if len(r.overall_suggestions) > 15 else r.overall_suggestions
            ocr_text = r.ocr_text[:15] + "..." if len(r.ocr_text) > 15 else r.ocr_text
            modified_mark = "✏️" if getattr(r, 'is_modified', False) else ""
            tree.insert("", tk.END, values=(
                "☐",
                r.display_id,
                r.filename,
                f"{r.total_score}/{r.total_max}",
                r.style_name,
                r.created_at.strftime("%Y-%m-%d %H:%M"),
                comment,
                suggestions,
                ocr_text,
                modified_mark
            ), tags=(str(r.id),))

    def _on_tree_checkbox_click(self, event, tree):
        """处理树形列表的复选框点击"""
        region = tree.identify("region", event.x, event.y)
        if region != "cell":
            return

        column = tree.identify_column(event.x)
        if column != "#1":
            return

        row_id = tree.identify_row(event.y)
        if not row_id:
            return

        item = tree.item(row_id)
        values = list(item["values"])
        values[0] = "☑" if values[0] == "☐" else "☐"
        tree.item(row_id, values=tuple(values))

    def _toggle_select_all(self, tree):
        """全选/取消全选"""
        select_all = self.history_select_all_var.get()
        for item in tree.get_children():
            values = list(tree.item(item, "values"))
            values[0] = "☑" if select_all else "☐"
            tree.item(item, values=tuple(values))

    def _get_selected_record_ids(self, tree):
        """获取选中的记录的真实ID列表"""
        selected_ids = []
        for item in tree.get_children():
            values = tree.item(item, "values")
            if values[0] == "☑":
                tags = tree.item(item, "tags")
                if tags:
                    selected_ids.append(int(tags[0]))
        return selected_ids

    def _delete_history_records(self, tree, user_id):
        """删除选中的历史记录"""
        selected_ids = self._get_selected_record_ids(tree)
        if not selected_ids:
            messagebox.showinfo("提示", "请先勾选要删除的记录")
            return

        if not messagebox.askyesno("确认", f"确定删除选中的 {len(selected_ids)} 条记录吗？此操作不可恢复！"):
            return

        results = self.history_mgr.delete_records(selected_ids)
        success_count = len(results["success"])
        fail_count = len(results["failed"])

        if fail_count > 0:
            messagebox.showwarning("结果", f"成功删除 {success_count} 条，失败 {fail_count} 条")
        else:
            messagebox.showinfo("成功", f"已删除 {success_count} 条记录")

        self._refresh_history_tree(tree, user_id)
        self._log(f"已删除 {success_count} 条历史记录", "success")

    def _send_history_email(self, tree, user_id):
        """发送历史记录邮件（支持多格式附件）"""
        selected_ids = self._get_selected_record_ids(tree)
        
        # 选择附件格式
        format_dialog = tk.Toplevel(self.root)
        format_dialog.title("选择附件格式")
        self._setup_dialog_window(format_dialog, width=520, height=380, min_width=420, min_height=300)
        
        theme = self._get_theme()
        format_dialog.configure(bg=theme["bg"])
        
        frame = ttk.Frame(format_dialog, padding="20")
        frame.pack(fill=tk.BOTH, expand=True)
        
        ttk.Label(frame, text="选择附件格式:").pack(pady=(0, 10))
        
        format_var = tk.StringVar(value="excel")
        for fmt, label in [("excel", "Excel (.xlsx)"), ("pdf", "PDF (.pdf)"), ("ppt", "PPT (.pptx)"), ("docx", "Word (.docx)")]:
            ttk.Radiobutton(frame, text=label, variable=format_var, value=fmt).pack(anchor=tk.W)
        
        def on_confirm():
            format_dialog.destroy()
            _do_send(format_var.get())
        
        ttk.Button(frame, text="确定", command=on_confirm).pack(pady=(15, 0))
        
        def _do_send(export_format: str):
            # 创建临时文件
            import tempfile
            ext_map = {"excel": ".xlsx", "pdf": ".pdf", "ppt": ".pptx", "docx": ".docx"}
            ext = ext_map.get(export_format, ".xlsx")
            temp_path = Path(tempfile.gettempdir()) / f"history_export_{int(datetime.now().timestamp())}{ext}"
            
            try:
                if selected_ids:
                    records = [r for r in self._current_history_records if r.id in selected_ids]
                else:
                    result = self.history_mgr.get_user_history(user_id, page=1, page_size=1000)
                    records = result["records"]
                
                if not records:
                    messagebox.showwarning("提示", "没有可发送的记录")
                    return
                
                # 导出临时文件
                scores = []
                filenames = []
                for r in records:
                    score_data = {
                        "total_score": r.total_score,
                        "total_max": r.total_max,
                        "percentage": r.percentage,
                        "dimensions": r.dimensions,
                        "overall_comment": r.overall_comment,
                        "overall_suggestions": r.overall_suggestions,
                        "strengths": r.strengths,
                        "weaknesses": r.weaknesses,
                    }
                    scores.append(score_data)
                    filenames.append(r.filename)
                
                if export_format == "excel":
                    self._export_records_to_excel(records, temp_path)
                elif export_format == "pdf":
                    self.exporter.export_pdf(scores, filenames, temp_path)
                elif export_format == "ppt":
                    self.exporter.export_ppt(scores, filenames, temp_path)
                elif export_format == "docx":
                    self.exporter.export_docx(scores, filenames, temp_path)
                
                subject = f"历史记录导出（{len(records)}条）"
                self._show_email_dialog_with_attachment(temp_path, subject, records=records)
                
            except Exception as e:
                messagebox.showerror("错误", f"准备附件失败: {e}")

    def _export_records_to_excel(self, records, output_path):
        """将指定记录导出为Excel（与主界面export_excel格式一致）"""
        from openpyxl import Workbook
        from openpyxl.styles import Font, Alignment, PatternFill, Border, Side
        
        wb = Workbook()
        ws = wb.active
        assert ws is not None
        ws.title = "历史记录"

        # === 样式定义（与exporter.py一致）===
        title_font = Font(name="微软雅黑", size=16, bold=True, color="FFFFFF")
        title_fill = PatternFill(start_color="4472C4", end_color="4472C4", fill_type="solid")
        title_alignment = Alignment(horizontal="center", vertical="center")

        header_font = Font(name="微软雅黑", size=11, bold=True, color="FFFFFF")
        header_fill = PatternFill(start_color="4472C4", end_color="4472C4", fill_type="solid")
        header_alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)

        cell_font = Font(name="微软雅黑", size=10)
        cell_alignment = Alignment(horizontal="left", vertical="top", wrap_text=True)
        cell_alignment_center = Alignment(horizontal="center", vertical="center", wrap_text=True)

        thin_border = Border(
            left=Side(style='thin', color='000000'),
            right=Side(style='thin', color='000000'),
            top=Side(style='thin', color='000000'),
            bottom=Side(style='thin', color='000000')
        )

        # === 标题行 ===
        ws.merge_cells("A1:I1")
        title_cell = ws["A1"]
        title_cell.value = "实验报告批阅历史记录"
        title_cell.font = title_font
        title_cell.fill = title_fill
        title_cell.alignment = title_alignment
        ws.row_dimensions[1].height = 35

        # === 日期行 ===
        ws.merge_cells("A2:I2")
        date_cell = ws["A2"]
        date_cell.value = f"生成日期: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        date_cell.font = Font(name="微软雅黑", size=10, italic=True, color="666666")
        date_cell.alignment = Alignment(horizontal="right", vertical="center")
        ws.row_dimensions[2].height = 25

        # === 表头 ===
        headers = ["ID", "文件名", "总分", "满分", "得分率", "风格", "评语", "建议", "时间"]
        for col, header in enumerate(headers, 1):
            cell = ws.cell(row=4, column=col, value=header)
            cell.font = header_font
            cell.fill = header_fill
            cell.alignment = header_alignment
            cell.border = thin_border
        ws.row_dimensions[4].height = 30

        # === 数据行 ===
        for i, r in enumerate(records, 1):
            row = i + 4

            # ID
            cell = ws.cell(row=row, column=1, value=r.display_id)
            cell.font = cell_font
            cell.alignment = cell_alignment_center
            cell.border = thin_border

            # 文件名
            cell = ws.cell(row=row, column=2, value=r.filename)
            cell.font = cell_font
            cell.alignment = cell_alignment_center
            cell.border = thin_border

            # 总分
            cell = ws.cell(row=row, column=3, value=r.total_score)
            cell.font = Font(name="微软雅黑", size=11, bold=True, color="C00000")
            cell.alignment = cell_alignment_center
            cell.border = thin_border

            # 满分
            cell = ws.cell(row=row, column=4, value=r.total_max)
            cell.font = cell_font
            cell.alignment = cell_alignment_center
            cell.border = thin_border

            # 得分率
            cell = ws.cell(row=row, column=5, value=f"{r.percentage}%")
            cell.font = cell_font
            cell.alignment = cell_alignment_center
            cell.border = thin_border

            # 风格
            cell = ws.cell(row=row, column=6, value=r.style_name)
            cell.font = cell_font
            cell.alignment = cell_alignment_center
            cell.border = thin_border

            # 评语（合并显示，换行）
            comment = r.overall_comment if r.overall_comment else "（无评语）"
            cell = ws.cell(row=row, column=7, value=comment)
            cell.font = cell_font
            cell.alignment = cell_alignment
            cell.border = thin_border

            # 建议（合并显示，换行）
            suggestions = r.overall_suggestions if r.overall_suggestions else "（无建议）"
            cell = ws.cell(row=row, column=8, value=suggestions)
            cell.font = cell_font
            cell.alignment = cell_alignment
            cell.border = thin_border

            # 时间
            cell = ws.cell(row=row, column=9, value=r.created_at.strftime("%Y-%m-%d %H:%M"))
            cell.font = cell_font
            cell.alignment = cell_alignment_center
            cell.border = thin_border

            # 行高（根据内容自动调整）
            ws.row_dimensions[row].height = 120

        # === 列宽（与exporter.py一致）===
        col_widths = {"A": 6, "B": 20, "C": 8, "D": 8, "E": 10, "F": 12, "G": 35, "H": 35, "I": 15}
        for col_letter, width in col_widths.items():
            ws.column_dimensions[col_letter].width = width

        # 冻结首行
        ws.freeze_panes = "A5"

        wb.save(output_path)

    def _show_email_dialog_with_attachment(self, attachment_path, default_subject, records=None):
        """显示发送邮件对话框（带附件）"""
        if not self.email_sender.is_configured():
            if not messagebox.askyesno("未配置", "SMTP未配置，是否现在配置？"):
                return
            self._show_smtp_config()
            return

        dialog = tk.Toplevel(self.root)
        dialog.title("发送历史记录")
        self._setup_dialog_window(dialog, width=760, height=640, min_width=560, min_height=460)
        theme = self._get_theme()
        dialog.configure(bg=theme["bg"])

        frame, footer = self._create_scrollable_dialog_body(dialog, padding="20")

        ttk.Label(frame, text="发送历史记录", font=("微软雅黑", 14, "bold")).pack(anchor=tk.W, pady=(0, 15))
        ttk.Label(frame, text=f"附件: {attachment_path.name}").pack(anchor=tk.W, pady=5)

        ttk.Label(frame, text="收件人邮箱:").pack(anchor=tk.W, pady=(10, 2))
        recipient_entry = ttk.Entry(frame, width=50)
        recipient_entry.pack(fill=tk.X, pady=2)

        ttk.Label(frame, text="邮件主题:").pack(anchor=tk.W, pady=(5, 2))
        subject_entry = ttk.Entry(frame, width=50)
        subject_entry.pack(fill=tk.X, pady=2)
        subject_entry.insert(0, default_subject)

        ttk.Label(frame, text="邮件正文:").pack(anchor=tk.W, pady=(10, 2))
        body_text = scrolledtext.ScrolledText(frame, wrap=tk.WORD, height=6)
        body_text.pack(fill=tk.BOTH, expand=True, pady=2)
        body_text.insert(tk.END, "您好！\n\n附件为实验报告批阅历史记录，请查收。\n\n祝好！")

        def build_history_score_context():
            """为历史记录邮件生成成绩上下文，避免重新生成智能邮件时报未定义错误。"""
            history_records = records or []
            if not history_records:
                return {
                    "percentage": 100,
                    "strengths": ["已整理完成历史批阅记录"],
                    "weaknesses": [],
                    "overall_suggestions": "附件中包含本次导出的历史批阅记录，请结合记录中的评语和建议查看。",
                }

            completed_items = []
            for record in history_records:
                score_data = {
                    "total_score": getattr(record, "total_score", 0),
                    "total_max": getattr(record, "total_max", 100),
                    "percentage": getattr(record, "percentage", 0),
                    "dimensions": getattr(record, "dimensions", {}),
                    "overall_comment": getattr(record, "overall_comment", ""),
                    "overall_suggestions": getattr(record, "overall_suggestions", ""),
                    "strengths": getattr(record, "strengths", []) or [],
                    "weaknesses": getattr(record, "weaknesses", []) or [],
                }
                completed_items.append((getattr(record, "filename", "历史记录"), {"score": score_data}))
            return self._build_email_score_context(completed_items)

        score_context = build_history_score_context()

        def refresh_ai_mail():
            template = self.email_dispatch_agent.compose(
                "老师/同学",
                score_context,
                attachment_label="历史批阅记录",
                subject=default_subject,
            )
            subject_entry.delete(0, tk.END)
            subject_entry.insert(0, template["subject"])
            body_text.delete(1.0, tk.END)
            body_text.insert(tk.END, template["body"])

        def on_send():
            recipient = recipient_entry.get().strip()
            subject = subject_entry.get().strip() or default_subject
            body = body_text.get(1.0, tk.END).strip()

            if not recipient:
                messagebox.showwarning("提示", "请填写收件人邮箱")
                return

            success, msg = self.email_sender.send_grading_result(
                recipient_email=recipient,
                recipient_name="老师/同学",
                attachment_path=attachment_path,
                subject=subject,
                message_body=body
            )

            if success:
                messagebox.showinfo("成功", msg)
                self._log(f"历史记录邮件已发送至 {recipient}", "success")
                dialog.destroy()
            else:
                messagebox.showerror("失败", msg)
                self._log(f"发送邮件失败: {msg}", "error")

        ttk.Button(footer, text="取消", command=dialog.destroy).pack(side=tk.RIGHT, padx=5)
        ttk.Button(footer, text="发送", command=on_send).pack(side=tk.RIGHT, padx=5)
        ttk.Button(footer, text="🤖 重新生成智能邮件", command=refresh_ai_mail).pack(side=tk.LEFT, padx=5)

    def _export_history_dialog_from_tree(self, tree, user_id, export_format: str = "excel"):
        """从树形列表导出历史记录（支持多格式）"""
        selected_ids = self._get_selected_record_ids(tree)
        
        # 文件类型映射
        format_map = {
            "excel": (".xlsx", "Excel文件", "*.xlsx"),
            "pdf": (".pdf", "PDF文件", "*.pdf"),
            "ppt": (".pptx", "PPT文件", "*.pptx"),
            "docx": (".docx", "Word文件", "*.docx"),
        }
        
        ext, file_type, file_pattern = format_map.get(export_format, (".xlsx", "Excel文件", "*.xlsx"))
        
        # 弹出选项对话框（排序 + 确认）
        sort_dialog = tk.Toplevel(self.root)
        sort_dialog.title("导出选项")
        self._setup_dialog_window(sort_dialog, width=440, height=300, min_width=380, min_height=260)
        
        theme = self._get_theme()
        sort_dialog.configure(bg=theme["bg"])
        
        frame = ttk.Frame(sort_dialog, padding="20")
        frame.pack(fill=tk.BOTH, expand=True)
        
        ttk.Label(frame, text=f"导出格式: {file_type}", font=("微软雅黑", 11, "bold")).pack(anchor=tk.W, pady=(0, 10))
        
        ttk.Label(frame, text="排序方式:").pack(anchor=tk.W, pady=(0, 5))
        sort_var = tk.StringVar(value="desc")
        ttk.Radiobutton(frame, text="倒序（最新在前）", variable=sort_var, value="desc").pack(anchor=tk.W)
        ttk.Radiobutton(frame, text="正序（最早在前）", variable=sort_var, value="asc").pack(anchor=tk.W)
        
        def on_confirm():
            sort_dialog.destroy()
            _do_export(sort_var.get())
        
        ttk.Button(frame, text="确定导出", command=on_confirm).pack(pady=(15, 0))
        
        def _do_export(sort_order: str):
            user = self.auth.get_current_user()
            filepath = filedialog.asksaveasfilename(
                defaultextension=ext,
                filetypes=[(file_type, file_pattern)],
                initialfile=f"history_{user.username if user else 'unknown'}_{int(datetime.now().timestamp())}{ext}"
            )
            if not filepath:
                return

            try:
                # 获取记录
                if selected_ids:
                    records = [r for r in self._current_history_records if r.id in selected_ids]
                else:
                    result = self.history_mgr.get_user_history(user_id, page=1, page_size=1000)
                    records = result["records"]
                
                if not records:
                    messagebox.showwarning("提示", "没有可导出的记录")
                    return
                
                # 排序
                if sort_order == "asc":
                    records = list(reversed(records))
                
                # 根据格式导出
                if export_format == "excel":
                    self._export_records_to_excel(records, Path(filepath))
                else:
                    # 转换为 exporter 需要的格式
                    scores = []
                    filenames = []
                    for r in records:
                        score_data = {
                            "total_score": r.total_score,
                            "total_max": r.total_max,
                            "percentage": r.percentage,
                            "dimensions": r.dimensions,
                            "overall_comment": r.overall_comment,
                            "overall_suggestions": r.overall_suggestions,
                            "strengths": r.strengths,
                            "weaknesses": r.weaknesses,
                        }
                        scores.append(score_data)
                        filenames.append(r.filename)
                    
                    if export_format == "pdf":
                        self.exporter.export_pdf(scores, filenames, Path(filepath))
                    elif export_format == "ppt":
                        self.exporter.export_ppt(scores, filenames, Path(filepath))
                    elif export_format == "docx":
                        self.exporter.export_docx(scores, filenames, Path(filepath))
                
                messagebox.showinfo("成功", f"已导出: {filepath}")
                self._log(f"历史记录已导出为 {file_type}: {filepath}（{'正序' if sort_order == 'asc' else '倒序'}）", "success")
                
            except Exception as e:
                messagebox.showerror("错误", f"导出失败: {e}")
                self._log(f"导出失败: {e}", "error")

    def _show_add_record_dialog(self, tree, user_id):
        """显示新增记录对话框"""
        dialog = tk.Toplevel(self.root)
        dialog.title("新增历史记录")
        dialog.geometry("700x750")
        dialog.transient(self.root)
        dialog.grab_set()

        theme = self._get_theme()
        dialog.configure(bg=theme["bg"])

        canvas = tk.Canvas(dialog, bg=theme["bg"])
        scrollbar = ttk.Scrollbar(dialog, orient="vertical", command=canvas.yview)
        scroll_frame = ttk.Frame(canvas, padding="20")

        scroll_frame.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )
        canvas.create_window((0, 0), window=scroll_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)

        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self._bind_canvas_mousewheel(canvas, dialog)

        ttk.Label(scroll_frame, text="新增历史记录", font=("微软雅黑", 14, "bold")).pack(anchor=tk.W, pady=(0, 15))

        next_id = self.db.get_next_display_id(user_id)
        ttk.Label(scroll_frame, text=f"记录ID: {next_id}（自动生成）", font=("微软雅黑", 10, "bold"), foreground="blue").pack(anchor=tk.W, pady=(0, 10))

        fields = {}
        field_configs = [
            ("filename", "文件名", ""),
            ("total_score", "总分", "0"),
            ("total_max", "满分", "100"),
            ("percentage", "得分率", "0"),
            ("style_key", "风格标识", "standard"),
            ("style_name", "风格名称", "标准风格"),
            ("quality_score", "质量分", ""),
        ]

        for key, label, default in field_configs:
            frame = ttk.Frame(scroll_frame)
            frame.pack(fill=tk.X, pady=3)
            ttk.Label(frame, text=f"{label}:", width=12, anchor=tk.E).pack(side=tk.LEFT)
            entry = ttk.Entry(frame, width=40)
            entry.pack(side=tk.LEFT, padx=5)
            entry.insert(0, default)
            fields[key] = entry

        dim_frame = ttk.LabelFrame(scroll_frame, text="维度评分（JSON格式）", padding="10")
        dim_frame.pack(fill=tk.X, pady=10)
        dim_text = scrolledtext.ScrolledText(dim_frame, wrap=tk.WORD, height=6, font=("Consolas", 9))
        dim_text.pack(fill=tk.BOTH, expand=True)
        dim_text.insert(tk.END, '''{
    "实验目的理解": {"score": 15, "max_score": 20, "comment": "评语", "suggestions": "建议"},
    "实验步骤完整性": {"score": 25, "max_score": 30, "comment": "评语", "suggestions": "建议"},
    "结果分析与数据处理": {"score": 25, "max_score": 30, "comment": "评语", "suggestions": "建议"},
    "结论与总结": {"score": 15, "max_score": 20, "comment": "评语", "suggestions": "建议"}
}''')
        fields["dimensions"] = dim_text

        for key, label, height in [("overall_comment", "总体评语", 4), ("overall_suggestions", "总体建议", 4),
                                    ("strengths", "优点（JSON数组）", 2), ("weaknesses", "不足（JSON数组）", 2),
                                    ("ocr_text", "OCR原文", 4), ("sections", "段落（JSON）", 3)]:
            frame = ttk.LabelFrame(scroll_frame, text=label, padding="5")
            frame.pack(fill=tk.X, pady=5)
            text = scrolledtext.ScrolledText(frame, wrap=tk.WORD, height=height, font=("微软雅黑", 9))
            text.pack(fill=tk.BOTH, expand=True)
            if key in ["strengths", "weaknesses"]:
                text.insert(tk.END, '["示例1", "示例2"]')
            elif key == "sections":
                text.insert(tk.END, '{"实验目的": "", "实验步骤": "", "实验结果": "", "实验结论": ""}')
            fields[key] = text

        def on_save():
            try:
                data = {
                    "filename": fields["filename"].get().strip(),
                    "total_score": int(fields["total_score"].get() or 0),
                    "total_max": int(fields["total_max"].get() or 100),
                    "percentage": float(fields["percentage"].get() or 0),
                    "style_key": fields["style_key"].get().strip(),
                    "style_name": fields["style_name"].get().strip(),
                    "quality_score": float(fields["quality_score"].get()) if fields["quality_score"].get() else None,
                    "dimensions": json.loads(fields["dimensions"].get(1.0, tk.END)),
                    "overall_comment": fields["overall_comment"].get(1.0, tk.END).strip(),
                    "overall_suggestions": fields["overall_suggestions"].get(1.0, tk.END).strip(),
                    "strengths": json.loads(fields["strengths"].get(1.0, tk.END)),
                    "weaknesses": json.loads(fields["weaknesses"].get(1.0, tk.END)),
                    "ocr_text": fields["ocr_text"].get(1.0, tk.END).strip(),
                    "sections": json.loads(fields["sections"].get(1.0, tk.END)),
                }
            except (json.JSONDecodeError, ValueError) as e:
                messagebox.showerror("错误", f"数据格式错误: {e}")
                return

            if not data["filename"]:
                messagebox.showwarning("提示", "文件名不能为空")
                return

            record_id = self.history_mgr.create_manual_record(user_id, data)
            if record_id > 0:
                messagebox.showinfo("成功", f"记录已新增（真实ID: {record_id}）")
                self._log(f"手动新增历史记录: {data['filename']}", "success")
                self._refresh_history_tree(tree, user_id)
                dialog.destroy()
            else:
                messagebox.showerror("失败", "新增记录失败")

        btn_frame = ttk.Frame(scroll_frame)
        btn_frame.pack(pady=(15, 0))
        ttk.Button(btn_frame, text="保存", command=on_save).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="取消", command=dialog.destroy).pack(side=tk.LEFT, padx=5)

    def _show_edit_record_dialog(self, tree, user_id):
        """显示改动记录对话框"""
        selected_ids = self._get_selected_record_ids(tree)
        if len(selected_ids) != 1:
            messagebox.showinfo("提示", "请勾选一条记录进行改动")
            return

        record = None
        for r in self._current_history_records:
            if r.id == selected_ids[0]:
                record = r
                break

        if not record:
            messagebox.showerror("错误", "记录不存在")
            return

        dialog = tk.Toplevel(self.root)
        dialog.title(f"改动记录 - ID:{record.display_id}")
        dialog.geometry("700x750")
        dialog.transient(self.root)
        dialog.grab_set()

        theme = self._get_theme()
        dialog.configure(bg=theme["bg"])

        canvas = tk.Canvas(dialog, bg=theme["bg"])
        scrollbar = ttk.Scrollbar(dialog, orient="vertical", command=canvas.yview)
        scroll_frame = ttk.Frame(canvas, padding="20")

        scroll_frame.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )
        canvas.create_window((0, 0), window=scroll_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)

        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self._bind_canvas_mousewheel(canvas, dialog)

        ttk.Label(scroll_frame, text=f"改动记录 ID:{record.display_id}（真实ID:{record.id}）", 
                  font=("微软雅黑", 14, "bold")).pack(anchor=tk.W, pady=(0, 15))
        ttk.Label(scroll_frame, text="提示: 保存后将标记为已改动", font=("微软雅黑", 9), foreground="gray").pack(anchor=tk.W, pady=(0, 10))

        fields = {}
        field_configs = [
            ("filename", "文件名", record.filename),
            ("total_score", "总分", str(record.total_score)),
            ("total_max", "满分", str(record.total_max)),
            ("percentage", "得分率", str(record.percentage)),
            ("style_key", "风格标识", record.style_key),
            ("style_name", "风格名称", record.style_name),
            ("quality_score", "质量分", str(record.quality_score) if record.quality_score else ""),
        ]

        for key, label, default in field_configs:
            frame = ttk.Frame(scroll_frame)
            frame.pack(fill=tk.X, pady=3)
            ttk.Label(frame, text=f"{label}:", width=12, anchor=tk.E).pack(side=tk.LEFT)
            entry = ttk.Entry(frame, width=40)
            entry.pack(side=tk.LEFT, padx=5)
            entry.insert(0, default)
            fields[key] = entry

        dim_frame = ttk.LabelFrame(scroll_frame, text="维度评分（JSON格式）", padding="10")
        dim_frame.pack(fill=tk.X, pady=10)
        dim_text = scrolledtext.ScrolledText(dim_frame, wrap=tk.WORD, height=6, font=("Consolas", 9))
        dim_text.pack(fill=tk.BOTH, expand=True)
        dim_text.insert(tk.END, json.dumps(record.dimensions, ensure_ascii=False, indent=2))
        fields["dimensions"] = dim_text

        text_fields = [
            ("overall_comment", "总体评语", record.overall_comment, 4),
            ("overall_suggestions", "总体建议", record.overall_suggestions, 4),
            ("strengths", "优点（JSON数组）", json.dumps(record.strengths, ensure_ascii=False), 2),
            ("weaknesses", "不足（JSON数组）", json.dumps(record.weaknesses, ensure_ascii=False), 2),
            ("ocr_text", "OCR原文", record.ocr_text, 4),
            ("sections", "段落（JSON）", json.dumps(record.sections, ensure_ascii=False, indent=2), 3),
        ]

        for key, label, default, height in text_fields:
            frame = ttk.LabelFrame(scroll_frame, text=label, padding="5")
            frame.pack(fill=tk.X, pady=5)
            text = scrolledtext.ScrolledText(frame, wrap=tk.WORD, height=height, font=("微软雅黑", 9))
            text.pack(fill=tk.BOTH, expand=True)
            text.insert(tk.END, default)
            fields[key] = text

        def on_save():
            try:
                update_data = {
                    "filename": fields["filename"].get().strip(),
                    "total_score": int(fields["total_score"].get() or 0),
                    "total_max": int(fields["total_max"].get() or 100),
                    "percentage": float(fields["percentage"].get() or 0),
                    "style_key": fields["style_key"].get().strip(),
                    "style_name": fields["style_name"].get().strip(),
                    "quality_score": float(fields["quality_score"].get()) if fields["quality_score"].get() else None,
                    "dimensions": json.loads(fields["dimensions"].get(1.0, tk.END)),
                    "overall_comment": fields["overall_comment"].get(1.0, tk.END).strip(),
                    "overall_suggestions": fields["overall_suggestions"].get(1.0, tk.END).strip(),
                    "strengths": json.loads(fields["strengths"].get(1.0, tk.END)),
                    "weaknesses": json.loads(fields["weaknesses"].get(1.0, tk.END)),
                    "ocr_text": fields["ocr_text"].get(1.0, tk.END).strip(),
                    "sections": json.loads(fields["sections"].get(1.0, tk.END)),
                }
            except (json.JSONDecodeError, ValueError) as e:
                messagebox.showerror("错误", f"数据格式错误: {e}")
                return

            if not update_data["filename"]:
                messagebox.showwarning("提示", "文件名不能为空")
                return

            # 调试输出
            print(f"更新数据: {update_data}")
            
            success = self.db.update_record_fields(record.id, **update_data)
            if success:
                messagebox.showinfo("成功", "记录已改动")
                self._log(f"改动历史记录 ID:{record.display_id}", "success")
                self._refresh_history_tree(tree, user_id)
                dialog.destroy()
            else:
                messagebox.showerror("失败", "改动记录失败，请检查日志")

        btn_frame = ttk.Frame(scroll_frame)
        btn_frame.pack(pady=(15, 0))
        ttk.Button(btn_frame, text="保存", command=on_save).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="取消", command=dialog.destroy).pack(side=tk.LEFT, padx=5)

    def _on_history_double_click(self, event, tree, user_id):
        """双击查看历史记录详情"""
        region = tree.identify("region", event.x, event.y)
        if region not in ("cell", "tree"):
            return

        row_id = tree.identify_row(event.y)
        if not row_id:
            return

        tree.selection_set(row_id)
        item = tree.item(row_id)
        tags = item.get("tags", ())
        if not tags:
            return

        real_id = int(tags[0])
        
        target_record = None
        for r in self._current_history_records:
            if r.id == real_id:
                target_record = r
                break

        if not target_record:
            return

        preview_dialog = tk.Toplevel(tree.winfo_toplevel())
        preview_dialog.title(f"📋 详情预览 - {target_record.filename}")
        preview_dialog.geometry("900x800")
        preview_dialog.transient(tree.winfo_toplevel())
        preview_dialog.grab_set()
        preview_dialog.resizable(True, True)

        theme = self._get_theme()
        preview_dialog.configure(bg=theme["bg"])

        p_frame = ttk.Frame(preview_dialog, padding="20")
        p_frame.pack(fill=tk.BOTH, expand=True)
        p_frame.columnconfigure(0, weight=1)

        info_frame = ttk.LabelFrame(p_frame, text="基本信息", padding="10")
        info_frame.pack(fill=tk.X, pady=(0, 15))
        info_frame.columnconfigure(1, weight=1)

        ttk.Label(info_frame, text="记录ID:", font=("微软雅黑", 10, "bold")).grid(row=0, column=0, sticky=tk.W, padx=5, pady=2)
        ttk.Label(info_frame, text=f"{target_record.display_id}（真实ID: {target_record.id}）", font=("微软雅黑", 10)).grid(row=0, column=1, sticky=tk.W, padx=5, pady=2)

        ttk.Label(info_frame, text="文件:", font=("微软雅黑", 10, "bold")).grid(row=1, column=0, sticky=tk.W, padx=5, pady=2)
        ttk.Label(info_frame, text=target_record.filename, font=("微软雅黑", 10)).grid(row=1, column=1, sticky=tk.W, padx=5, pady=2)

        ttk.Label(info_frame, text="分数:", font=("微软雅黑", 10, "bold")).grid(row=2, column=0, sticky=tk.W, padx=5, pady=2)
        ttk.Label(info_frame, text=f"{target_record.total_score}/{target_record.total_max} ({target_record.percentage}%)", font=("微软雅黑", 10)).grid(row=2, column=1, sticky=tk.W, padx=5, pady=2)

        ttk.Label(info_frame, text="风格:", font=("微软雅黑", 10, "bold")).grid(row=3, column=0, sticky=tk.W, padx=5, pady=2)
        ttk.Label(info_frame, text=target_record.style_name, font=("微软雅黑", 10)).grid(row=3, column=1, sticky=tk.W, padx=5, pady=2)

        ttk.Label(info_frame, text="时间:", font=("微软雅黑", 10, "bold")).grid(row=4, column=0, sticky=tk.W, padx=5, pady=2)
        ttk.Label(info_frame, text=target_record.created_at.strftime('%Y-%m-%d %H:%M:%S'), font=("微软雅黑", 10)).grid(row=4, column=1, sticky=tk.W, padx=5, pady=2)

        if getattr(target_record, 'is_modified', False):
            ttk.Label(info_frame, text="状态:", font=("微软雅黑", 10, "bold")).grid(row=5, column=0, sticky=tk.W, padx=5, pady=2)
            ttk.Label(info_frame, text="✏️ 已改动", font=("微软雅黑", 10), foreground="orange").grid(row=5, column=1, sticky=tk.W, padx=5, pady=2)

        comment_frame = ttk.LabelFrame(p_frame, text="📋 总体评语", padding="10")
        comment_frame.pack(fill=tk.BOTH, expand=True, pady=(0, 10))
        comment_frame.columnconfigure(0, weight=1)
        comment_frame.rowconfigure(0, weight=1)

        comment_text = tk.Text(comment_frame, wrap=tk.WORD, height=5, font=("微软雅黑", 10),
                               bg=theme["text_bg"], fg=theme["text_fg"],
                               insertbackground=theme["fg"],
                               selectbackground=theme.get("text_select_bg", theme["accent"]),
                               relief=tk.FLAT, padx=5, pady=5)
        comment_text.grid(row=0, column=0, sticky="nsew")
        comment_scroll = ttk.Scrollbar(comment_frame, orient="vertical", command=comment_text.yview)
        comment_scroll.grid(row=0, column=1, sticky="ns")
        comment_text.configure(yscrollcommand=comment_scroll.set)
        comment_text.insert(tk.END, target_record.overall_comment if target_record.overall_comment else "（无评语）")
        comment_text.configure(state=tk.DISABLED)

        suggestions_frame = ttk.LabelFrame(p_frame, text="💡 总体建议", padding="10")
        suggestions_frame.pack(fill=tk.BOTH, expand=True, pady=(0, 15))
        suggestions_frame.columnconfigure(0, weight=1)
        suggestions_frame.rowconfigure(0, weight=1)

        suggestions_text = tk.Text(suggestions_frame, wrap=tk.WORD, height=5, font=("微软雅黑", 10),
                                   bg=theme["text_bg"], fg=theme["text_fg"],
                                   insertbackground=theme["fg"],
                                   selectbackground=theme.get("text_select_bg", theme["accent"]),
                                   relief=tk.FLAT, padx=5, pady=5)
        suggestions_text.grid(row=0, column=0, sticky="nsew")
        suggestions_scroll = ttk.Scrollbar(suggestions_frame, orient="vertical", command=suggestions_text.yview)
        suggestions_scroll.grid(row=0, column=1, sticky="ns")
        suggestions_text.configure(yscrollcommand=suggestions_scroll.set)
        suggestions_text.insert(tk.END, target_record.overall_suggestions if target_record.overall_suggestions else "（无建议）")
        suggestions_text.configure(state=tk.DISABLED)

        # OCR原文
        ocr_frame = ttk.LabelFrame(p_frame, text="📝 OCR原文", padding="10")
        ocr_frame.pack(fill=tk.BOTH, expand=True, pady=(0, 15))
        ocr_frame.columnconfigure(0, weight=1)
        ocr_frame.rowconfigure(0, weight=1)

        ocr_text = tk.Text(ocr_frame, wrap=tk.WORD, height=6, font=("微软雅黑", 10),
                           bg=theme["text_bg"], fg=theme["text_fg"],
                           insertbackground=theme["fg"],
                           selectbackground=theme.get("text_select_bg", theme["accent"]),
                           relief=tk.FLAT, padx=5, pady=5)
        ocr_text.grid(row=0, column=0, sticky="nsew")
        ocr_scroll = ttk.Scrollbar(ocr_frame, orient="vertical", command=ocr_text.yview)
        ocr_scroll.grid(row=0, column=1, sticky="ns")
        ocr_text.configure(yscrollcommand=ocr_scroll.set)
        ocr_text.insert(tk.END, target_record.ocr_text if target_record.ocr_text else "（无OCR原文）")
        ocr_text.configure(state=tk.DISABLED)

        btn_frame = ttk.Frame(p_frame)
        btn_frame.pack(pady=(5, 0))
        ttk.Button(btn_frame, text="✓ 关闭", command=preview_dialog.destroy, width=15).pack()
    
    
    def _export_history_dialog(self):
        """导出历史记录"""
        user = self.auth.get_current_user()
        if not user:
            messagebox.showwarning("提示", "请先登录")
            return
        
        # 弹出排序选项对话框
        sort_dialog = tk.Toplevel(self.root)
        sort_dialog.title("导出选项")
        self._setup_dialog_window(sort_dialog, width=440, height=340, min_width=380, min_height=280)
        
        theme = self._get_theme()
        sort_dialog.configure(bg=theme["bg"])
        
        frame = ttk.Frame(sort_dialog, padding="20")
        frame.pack(fill=tk.BOTH, expand=True)
        
        ttk.Label(frame, text="排序方式:").pack(anchor=tk.W, pady=(0, 10))
        
        sort_var = tk.StringVar(value="desc")
        ttk.Radiobutton(frame, text="倒序（最新在前）", variable=sort_var, value="desc").pack(anchor=tk.W)
        ttk.Radiobutton(frame, text="正序（最早在前）", variable=sort_var, value="asc").pack(anchor=tk.W)
        
        def on_confirm():
            sort_dialog.destroy()
            _do_export(sort_var.get())
        
        ttk.Button(frame, text="确定", command=on_confirm).pack(pady=(15, 0))
        
        def _do_export(sort_order: str):
            filepath = filedialog.asksaveasfilename(
                defaultextension=".xlsx",
                filetypes=[("Excel文件", "*.xlsx")],
                initialfile=f"history_{user.username}_{int(datetime.now().timestamp())}.xlsx"
            )
            if not filepath:
                return
            
            if self.history_mgr.export_history_to_excel(user.id, Path(filepath), sort_order=sort_order):
                messagebox.showinfo("成功", f"历史记录已导出: {filepath}")
                self._log(f"历史记录已导出: {filepath}（{ '正序' if sort_order == 'asc' else '倒序' }）", "success")
            else:
                messagebox.showerror("失败", "导出失败")

    def _export_excel(self):
        self._log_behavior("export_excel")
        """导出Excel"""
        completed_items = [(name, data) for name, data in self.reports.items()
                          if data.get("score") and data.get("status") == "已完成"]
        if not completed_items:
            messagebox.showwarning("警告", "没有已完成的评分结果！")
            return

        filepath = filedialog.asksaveasfilename(
            defaultextension=".xlsx",
            filetypes=[("Excel文件", "*.xlsx")]
        )
        if not filepath:
            return

        try:
            filenames = [name for name, _ in completed_items]
            scores = [data["score"] for _, data in completed_items]
            self.exporter.export_excel(scores, filenames, Path(filepath))
            self._log(f"Excel已导出: {filepath}", "success")
            messagebox.showinfo("成功", "Excel导出成功！")
        except Exception as e:
            messagebox.showerror("错误", f"导出失败: {e}")

    def _export_pdf(self):
        self._log_behavior("export_pdf")
        """导出PDF"""
        completed_items = [(name, data) for name, data in self.reports.items()
                          if data.get("score") and data.get("status") == "已完成"]
        if not completed_items:
            messagebox.showwarning("警告", "没有已完成的评分结果！")
            return

        filepath = filedialog.asksaveasfilename(
            defaultextension=".pdf",
            filetypes=[("PDF文件", "*.pdf")]
        )
        if not filepath:
            return

        try:
            filenames = [name for name, _ in completed_items]
            scores = [data["score"] for _, data in completed_items]
            self.exporter.export_pdf(scores, filenames, Path(filepath))
            self._log(f"PDF已导出: {filepath}", "success")
            messagebox.showinfo("成功", "PDF导出成功！")
        except Exception as e:
            messagebox.showerror("错误", f"导出失败: {e}")

    def _export_ppt(self):
        self._log_behavior("export_ppt")
        """导出PPT"""
        completed_items = [(name, data) for name, data in self.reports.items()
                        if data.get("score") and data.get("status") == "已完成"]
        if not completed_items:
            messagebox.showwarning("警告", "没有已完成的评分结果！")
            return

        filepath = filedialog.asksaveasfilename(
            defaultextension=".pptx",
            filetypes=[("PPT文件", "*.pptx")]
        )
        if not filepath:
            return

        try:
            filenames = [name for name, _ in completed_items]
            scores = [data["score"] for _, data in completed_items]
            self.exporter.export_ppt(scores, filenames, Path(filepath))
            self._log(f"PPT已导出: {filepath}", "success")
            messagebox.showinfo("成功", "PPT导出成功！")
        except Exception as e:
            messagebox.showerror("错误", f"导出失败: {e}")

    def _export_docx(self):
        self._log_behavior("export_docx")
        """导出DOCX"""
        completed_items = [(name, data) for name, data in self.reports.items()
                        if data.get("score") and data.get("status") == "已完成"]
        if not completed_items:
            messagebox.showwarning("警告", "没有已完成的评分结果！")
            return

        filepath = filedialog.asksaveasfilename(
            defaultextension=".docx",
            filetypes=[("Word文件", "*.docx")]
        )
        if not filepath:
            return

        try:
            filenames = [name for name, _ in completed_items]
            scores = [data["score"] for _, data in completed_items]
            self.exporter.export_docx(scores, filenames, Path(filepath))
            self._log(f"DOCX已导出: {filepath}", "success")
            messagebox.showinfo("成功", "Word导出成功！")
        except Exception as e:
            messagebox.showerror("错误", f"导出失败: {e}")

    def _show_shortcuts(self):
        """显示快捷键说明"""
        shortcuts = """快捷键说明：

【文件操作】
Ctrl + O  - 导入报告文件（支持多选）
Ctrl + F  - 导入文件夹（递归导入所有支持格式）

【批阅操作】
Ctrl + G  - 开始批阅所有待处理报告
Ctrl + S  - 停止当前批阅任务
Ctrl + Shift + G  - 继续批阅停止后待继续/默认50分/失败的报告
Ctrl + R  - 重新批阅选中的报告（支持覆盖/新增选项）

【导出操作】
Ctrl + E  - 导出Excel
Ctrl + P  - 导出PDF

【其他功能】
Ctrl + M  - 发送邮件（需先配置SMTP）
Ctrl + D  - 打开数据仪表盘（统计/分布/雷达图/趋势/对比）
Ctrl + T  - 切换浅色/深色主题

【列表操作】
Delete    - 删除选中的报告

【拖拽功能】
直接将文件或文件夹拖入窗口即可上传
"""
        messagebox.showinfo("快捷键", shortcuts)

    def _show_about(self):
        """显示关于信息"""
        about = """实验报告批阅智能体 4.0

【项目简介】
自动识别学生提交的实验报告（图片/PDF/Word），
通过OCR提取文字，利用LLM进行内容理解与智能评分，
生成结构化的批阅建议。

【核心技术】
• Tesseract + Pillow：图像预处理与OCR识别
• LangChain + Kimi API：智能评分链构建
• tkinter：轻量级GUI界面
• MySQL：多用户数据持久化
• matplotlib：数据可视化仪表盘

【主要功能】
• 多格式支持：PNG/JPG/PDF/DOCX
• 5种批阅风格：标准/严格/温和/科研/工程（支持自定义）
• 智能防过载：串行队列 + 自适应退避机制
• 动态模型选择：根据文本长度自动切换8k/32k/128k模型
• 多用户系统：注册/登录/自动登录/个人资料管理
• 历史记录：查看/搜索/排序/删除/新增/改动/重新批阅
• 数据仪表盘：统计概览/分数分布/维度雷达图/趋势分析/批量对比
• 多格式导出：Excel/PDF/PPT/Word
• 邮件发送：支持SMTP配置，可发送带附件的批阅结果
• 主题切换：浅色/深色模式
• 文件拖拽：Windows原生拖拽上传

【开发信息】
软件工程专业 · 大三年级 · 小组实训项目
小组成员：刘赛、陈奎、邱三元
开发周期：2026.6.22 — 2026.7.1
Python 3.12.10 | VS Code | Black + Ruff
"""
        messagebox.showinfo("关于", about)

    def run(self):
        """启动GUI"""
        # 只有在主界面已加载（log_text 存在）时才写入启动日志
        if hasattr(self, "log_text") and self.log_text is not None:
            self._log("实验报告批阅智能体 4.0 已启动")
            self._log("快捷键: Ctrl+O导入, Ctrl+F文件夹, Ctrl+G批阅, Ctrl+E导出Excel, Ctrl+P导出PDF")
            self._log("          Ctrl+M发送邮件, Ctrl+D仪表盘, Ctrl+T切换主题")
            self._log("拖拽提示: 直接将文件拖入窗口即可上传")
        self.root.mainloop()