"""
用户认证模块
负责：用户注册、登录、密码哈希、自动登录、会话管理
"""

import hashlib
import secrets
import json
import logging
from pathlib import Path
from typing import Optional, Tuple

from modules.database import DatabaseManager, User

logger = logging.getLogger(__name__)

# 本地自动登录配置缓存
AUTO_LOGIN_FILE = Path(__file__).parent / ".cache" / "auto_login.json"


class AuthManager:
    """用户认证管理器"""

    def __init__(self, db: DatabaseManager):
        self.db = db
        self.current_user: Optional[User] = None

    @staticmethod
    def _hash_password(password: str, salt: Optional[str] = None) -> Tuple[str, str]:
        """密码哈希（PBKDF2）"""
        if salt is None:
            salt = secrets.token_hex(16)
        hash_value = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'),
                                         salt.encode('utf-8'), 100000).hex()
        return hash_value, salt

    def register(self, username: str, password: str) -> Tuple[bool, str]:
        """用户注册"""
        if not username or not password:
            return False, "用户名和密码不能为空"

        if len(username) < 3:
            return False, "用户名至少3个字符"

        if len(password) < 6:
            return False, "密码至少6个字符"

        # 检查用户名是否已存在
        existing = self.db.get_user_by_username(username)
        if existing:
            return False, "用户名已存在"

        # 创建用户
        password_hash, salt = self._hash_password(password)
        stored_hash = f"{salt}${password_hash}"

        user = self.db.create_user(username, stored_hash)
        if user:
            logger.info(f"用户注册成功: {username}")
            return True, "注册成功"
        return False, "注册失败，请稍后重试"

    def get_all_usernames(self) -> list[str]:
        """获取所有已注册用户名"""
        return self.db.get_all_usernames()
    
    def delete_user(self, username: str) -> Tuple[bool, str]:
        """删除用户及其所有相关数据"""
        if not username:
            return False, "用户名不能为空"
        
        # 获取用户
        user = self.db.get_user_by_username(username)
        if not user:
            return False, "用户不存在"
        
        try:
            # 1. 删除用户的所有批阅记录
            self.db.delete_user_records(user.id)
            
            # 2. 删除用户账号
            # 需要在 database.py 中添加 delete_user 方法
            self.db.delete_user(user.id)
            
            # 3. 如果删除的是当前登录用户，清除自动登录
            if self.current_user and self.current_user.username == username:
                self.logout()
            
            logger.info(f"用户已删除: {username}")
            return True, f"用户 '{username}' 及其所有数据已删除"
        except Exception as e:
            logger.error(f"删除用户失败: {e}")
            return False, f"删除失败: {e}"

    def login(self, username: str, password: str, auto_login: bool = False) -> Tuple[bool, str]:
        """用户登录"""
        if not username or not password:
            return False, "用户名和密码不能为空"

        user = self.db.get_user_by_username(username)
        if not user:
            return False, "用户名或密码错误"

        # 验证密码
        stored = user.password_hash
        if "$" not in stored:
            return False, "密码格式错误"

        salt, stored_hash = stored.split("$", 1)
        hash_value, _ = self._hash_password(password, salt)

        if hash_value != stored_hash:
            return False, "用户名或密码错误"

        # 登录成功
        self.current_user = user
        self.db.update_last_login(user.id)

        # 更新自动登录设置
        if auto_login != user.auto_login:
            self.db.update_auto_login(user.id, auto_login)

        # 保存自动登录凭证
        if auto_login:
            self._save_auto_login(user.id)
        else:
            self._clear_auto_login()

        logger.info(f"用户登录成功: {username}")
        return True, "登录成功"

    def logout(self):
        """退出登录"""
        if self.current_user:
            logger.info(f"用户退出: {self.current_user.username}")
        self.current_user = None
        self._clear_auto_login()

    def check_auto_login(self) -> Optional[User]:
        """检查自动登录"""
        if not AUTO_LOGIN_FILE.exists():
            return None

        try:
            with open(AUTO_LOGIN_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)

            user_id = data.get('user_id')
            token = data.get('token')

            if not user_id or not token:
                return None

            user = self.db.get_user_by_id(user_id)
            if not user or not user.auto_login:
                return None

            # 验证token（简单验证：用户ID + 盐哈希）
            expected = hashlib.sha256(f"{user_id}{user.password_hash[:32]}".encode()).hexdigest()
            if token != expected:
                return None

            self.current_user = user
            self.db.update_last_login(user.id)
            logger.info(f"自动登录成功: {user.username}")
            return user

        except Exception as e:
            logger.warning(f"自动登录检查失败: {e}")
            return None

    def _save_auto_login(self, user_id: int):
        """保存自动登录凭证"""
        try:
            AUTO_LOGIN_FILE.parent.mkdir(parents=True, exist_ok=True)
            user = self.db.get_user_by_id(user_id)
            if not user:
                return
            token = hashlib.sha256(f"{user_id}{user.password_hash[:32]}".encode()).hexdigest()
            with open(AUTO_LOGIN_FILE, 'w', encoding='utf-8') as f:
                json.dump({'user_id': user_id, 'token': token}, f)
        except Exception as e:
            logger.warning(f"保存自动登录失败: {e}")

    def _clear_auto_login(self):
        """清除自动登录凭证"""
        try:
            if AUTO_LOGIN_FILE.exists():
                AUTO_LOGIN_FILE.unlink()
        except Exception:
            pass

    def get_current_user(self) -> Optional[User]:
        """获取当前登录用户"""
        return self.current_user

    def is_logged_in(self) -> bool:
        """是否已登录"""
        return self.current_user is not None