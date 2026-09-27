"""
文件拖拽模块（Windows）
负责：支持文件拖拽到tkinter窗口上传
"""

import tkinter as tk
import logging
import ctypes
from ctypes import wintypes

logger = logging.getLogger(__name__)

# LONG_PTR 类型兼容处理
LONG_PTR = getattr(wintypes, "LONG_PTR", ctypes.c_void_p)

# ========== Windows API 类型定义 ==========

# 窗口过程回调类型
WNDPROC = ctypes.WINFUNCTYPE(
    wintypes.LPARAM,
    wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM
)

# DragAcceptFiles
ctypes.windll.shell32.DragAcceptFiles.argtypes = [wintypes.HWND, wintypes.BOOL]
ctypes.windll.shell32.DragAcceptFiles.restype = None

# DragQueryFileW
ctypes.windll.shell32.DragQueryFileW.argtypes = [wintypes.HANDLE, wintypes.UINT, wintypes.LPWSTR, wintypes.UINT]
ctypes.windll.shell32.DragQueryFileW.restype = wintypes.UINT

# DragFinish
ctypes.windll.shell32.DragFinish.argtypes = [wintypes.HANDLE]
ctypes.windll.shell32.DragFinish.restype = None

# SetWindowLongPtrW / GetWindowLongPtrW
try:
    SetWindowLongPtr = ctypes.windll.user32.SetWindowLongPtrW
    GetWindowLongPtr = ctypes.windll.user32.GetWindowLongPtrW
except AttributeError:
    # 32位系统
    SetWindowLongPtr = ctypes.windll.user32.SetWindowLongW
    GetWindowLongPtr = ctypes.windll.user32.GetWindowLongW

SetWindowLongPtr.argtypes = [wintypes.HWND, wintypes.INT, LONG_PTR]
SetWindowLongPtr.restype = LONG_PTR

GetWindowLongPtr.argtypes = [wintypes.HWND, wintypes.INT]
GetWindowLongPtr.restype = LONG_PTR

# CallWindowProcW
ctypes.windll.user32.CallWindowProcW.argtypes = [
    LONG_PTR, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM
]
ctypes.windll.user32.CallWindowProcW.restype = wintypes.LPARAM

GWL_WNDPROC = -4
WM_DROPFILES = 0x0233


class DragDropHandler:
    """文件拖拽处理器（Windows WM_DROPFILES）"""

    # 类级别引用，防止回调被垃圾回收
    _active_handlers = {}

    def __init__(self, root: tk.Tk, drop_callback):
        self.root = root
        self.drop_callback = drop_callback
        self._enabled = False
        self._original_wndproc = None
        self._hwnd = None
        self._new_wndproc_ref = None

    def enable(self):
        """启用拖拽功能"""
        if self._enabled:
            return

        try:
            self._setup_wm_dropfiles()
            self._enabled = True
            logger.info("文件拖拽功能已启用")
        except Exception as e:
            logger.warning(f"启用拖拽功能失败: {e}")
            self._enabled = False

    def _setup_wm_dropfiles(self):
        """使用Windows WM_DROPFILES消息处理拖拽"""
        # 获取窗口句柄（直接使用winfo_id，不使用GetParent）
        self._hwnd = self.root.winfo_id()
        if not self._hwnd:
            raise RuntimeError("无法获取窗口句柄")

        logger.info(f"窗口句柄: {self._hwnd}")

        # 注册为拖拽目标
        ctypes.windll.shell32.DragAcceptFiles(self._hwnd, True)

        # 保存原始窗口过程
        self._original_wndproc = GetWindowLongPtr(self._hwnd, GWL_WNDPROC)
        if not self._original_wndproc:
            logger.warning("原始窗口过程为0，可能有问题")

        # 创建新的窗口过程
        handler = self

        def py_wndproc(hwnd, msg, wparam, lparam):
            if msg == WM_DROPFILES:
                handler._handle_dropfiles(wparam)
                return 0
            # 调用原始窗口过程
            if handler._original_wndproc:
                return ctypes.windll.user32.CallWindowProcW(
                    handler._original_wndproc, hwnd, msg, wparam, lparam
                )
            return 0

        self._new_wndproc_ref = WNDPROC(py_wndproc)
        # 在类级别保存引用，防止垃圾回收
        DragDropHandler._active_handlers[self._hwnd] = self

        # 设置新的窗口过程 - 将WNDPROC转换为整数指针
        new_proc_ptr = ctypes.cast(self._new_wndproc_ref, LONG_PTR)
        result = SetWindowLongPtr(self._hwnd, GWL_WNDPROC, new_proc_ptr)
        if result == 0:
            err = ctypes.get_last_error()
            if err != 0:
                raise RuntimeError(f"SetWindowLongPtrW失败，错误码: {err}")

        logger.info("WM_DROPFILES 拖拽已启用")

    def _handle_dropfiles(self, hdrop):
        """处理WM_DROPFILES消息"""
        try:
            # 获取文件数量
            file_count = ctypes.windll.shell32.DragQueryFileW(hdrop, 0xFFFFFFFF, None, 0)
            logger.info(f"检测到 {file_count} 个拖拽文件")

            files = []
            for i in range(file_count):
                # 获取文件名长度
                buf_size = ctypes.windll.shell32.DragQueryFileW(hdrop, i, None, 0)
                if buf_size == 0:
                    continue
                # 获取文件名
                buffer = ctypes.create_unicode_buffer(buf_size + 1)
                ctypes.windll.shell32.DragQueryFileW(hdrop, i, buffer, buf_size + 1)
                if buffer.value:
                    files.append(buffer.value)

            ctypes.windll.shell32.DragFinish(hdrop)

            if files and self.drop_callback:
                logger.info(f"拖拽文件: {files}")
                # 使用after确保在主线程中执行回调
                self.root.after(10, lambda f=files: self.drop_callback(f))

        except Exception as e:
            logger.error(f"处理拖拽文件失败: {e}")

    def disable(self):
        """禁用拖拽功能"""
        if not self._enabled:
            return

        try:
            if self._original_wndproc and self._hwnd:
                SetWindowLongPtr(self._hwnd, GWL_WNDPROC, self._original_wndproc)

            if self._hwnd:
                ctypes.windll.shell32.DragAcceptFiles(self._hwnd, False)

            if self._hwnd in DragDropHandler._active_handlers:
                del DragDropHandler._active_handlers[self._hwnd]

        except Exception as e:
            logger.warning(f"禁用拖拽失败: {e}")

        self._enabled = False
        self._new_wndproc_ref = None
        logger.info("文件拖拽功能已禁用")

    def is_enabled(self) -> bool:
        return self._enabled