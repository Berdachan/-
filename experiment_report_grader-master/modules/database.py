"""
MySQL 数据库模块
负责：用户表、批阅历史记录表、数据库连接管理
"""

import logging
import json
from datetime import datetime
from typing import Optional, List, Dict, Any
from dataclasses import dataclass

try:
    import mysql.connector as mysql_connector  # type: ignore
    from mysql.connector import Error as MySQLError  # type: ignore
    from mysql.connector import pooling as mysql_pooling  # type: ignore
    MYSQL_AVAILABLE = True
except ImportError:
    MYSQL_AVAILABLE = False
    mysql_connector = None  # type: ignore
    MySQLError = Exception  # type: ignore
    mysql_pooling = None  # type: ignore

from config import DB_CONFIG

logger = logging.getLogger(__name__)


@dataclass
class User:
    """用户数据模型"""
    id: int
    username: str
    password_hash: str
    created_at: datetime
    auto_login: bool = False
    nickname: Optional[str] = None
    email: Optional[str] = None
    student_id: Optional[str] = None
    major: Optional[str] = None
    phone: Optional[str] = None
    bio: Optional[str] = None
    avatar_path: Optional[str] = None
    grade: Optional[str] = None
    experiment_course: Optional[str] = None


@dataclass
class GradingRecord:
    """批阅记录数据模型"""
    id: int
    user_id: int
    filename: str
    file_path: str
    total_score: int
    total_max: int
    percentage: float
    dimensions: Dict[str, Any]
    overall_comment: str
    overall_suggestions: str
    strengths: List[str]
    weaknesses: List[str]
    ocr_text: str
    sections: Dict[str, str]
    quality_score: Optional[float]
    style_key: str
    style_name: str
    created_at: datetime
    updated_at: datetime
    display_id: int = 0
    is_modified: bool = False


class DatabaseManager:
    """MySQL 数据库管理器"""

    def __init__(self):
        if not MYSQL_AVAILABLE:
            raise ImportError(
                "mysql-connector-python 未安装。\n"
                "请运行: pip install mysql-connector-python"
            )
        self.pool: Optional[mysql_pooling.MySQLConnectionPool] = None  # type: ignore
        self._init_pool()
        self._init_tables()

    def _init_pool(self) -> None:
        """初始化连接池"""
        try:
            self.pool = mysql_pooling.MySQLConnectionPool(  # type: ignore
                pool_name="grading_pool",
                pool_size=5,
                **DB_CONFIG
            )
            logger.info("MySQL连接池初始化成功")
        except MySQLError as e:
            logger.error(f"MySQL连接池初始化失败: {e}")
            raise

    def _get_connection(self):
        """获取数据库连接"""
        if self.pool is None:
            raise RuntimeError("数据库连接池未初始化")
        return self.pool.get_connection()

    def _init_tables(self) -> None:
        """初始化数据库表"""
        conn = self._get_connection()
        cursor = conn.cursor()

        # 用户表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INT AUTO_INCREMENT PRIMARY KEY,
                username VARCHAR(50) UNIQUE NOT NULL,
                password_hash VARCHAR(255) NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                auto_login BOOLEAN DEFAULT FALSE,
                last_login TIMESTAMP NULL,
                nickname VARCHAR(50),
                email VARCHAR(100),
                student_id VARCHAR(50),
                major VARCHAR(100),
                phone VARCHAR(20),
                bio TEXT,
                avatar_path VARCHAR(500),
                grade VARCHAR(50),
                experiment_course VARCHAR(100)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """)

        # 批阅记录表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS grading_records (
                id INT AUTO_INCREMENT PRIMARY KEY,
                user_id INT NOT NULL,
                filename VARCHAR(255) NOT NULL,
                file_path VARCHAR(500),
                total_score INT,
                total_max INT,
                percentage DECIMAL(5,2),
                dimensions JSON,
                overall_comment TEXT,
                overall_suggestions TEXT,
                strengths JSON,
                weaknesses JSON,
                ocr_text LONGTEXT,
                sections JSON,
                quality_score DECIMAL(5,2),
                style_key VARCHAR(50),
                style_name VARCHAR(50),
                is_aborted BOOLEAN DEFAULT FALSE,
                is_modified BOOLEAN DEFAULT FALSE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
                INDEX idx_user_id (user_id),
                INDEX idx_filename (filename),
                INDEX idx_created_at (created_at)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """)

        self._ensure_user_profile_columns(cursor)

        conn.commit()
        cursor.close()
        conn.close()
        logger.info("数据库表初始化完成")

    def _ensure_user_profile_columns(self, cursor) -> None:
        """为旧版本数据库补齐个人资料扩展字段。"""
        extra_columns = {
            "grade": "VARCHAR(50)",
            "experiment_course": "VARCHAR(100)",
        }
        for column, definition in extra_columns.items():
            try:
                cursor.execute("SHOW COLUMNS FROM users LIKE %s", (column,))
                exists = cursor.fetchone()
                if not exists:
                    cursor.execute(f"ALTER TABLE users ADD COLUMN {column} {definition}")
                    logger.info(f"已补齐 users.{column} 字段")
            except Exception as e:
                logger.warning(f"检查/补齐 users.{column} 字段失败: {e}")

    # ========== 用户管理 ==========

    def create_user(self, username: str, password_hash: str) -> Optional[User]:
        """创建用户"""
        conn = self._get_connection()
        cursor = conn.cursor()
        try:
            cursor.execute(
                "INSERT INTO users (username, password_hash) VALUES (%s, %s)",
                (username, password_hash)
            )
            conn.commit()
            user_id = cursor.lastrowid
            return User(id=user_id, username=username, password_hash=password_hash,
                       created_at=datetime.now())
        except MySQLError as e:
            logger.error(f"创建用户失败: {e}")
            return None
        finally:
            cursor.close()
            conn.close()

    def get_user_by_username(self, username: str) -> Optional[User]:
        """通过用户名获取用户"""
        conn = self._get_connection()
        cursor = conn.cursor(dictionary=True)
        try:
            cursor.execute("SELECT * FROM users WHERE username = %s", (username,))
            row = cursor.fetchone()
            if row:
                return User(
                    id=row["id"],
                    username=row["username"],
                    password_hash=row["password_hash"],
                    created_at=row["created_at"],
                    auto_login=row["auto_login"],
                    nickname=row.get("nickname"),
                    email=row.get("email"),
                    student_id=row.get("student_id"),
                    major=row.get("major"),
                    phone=row.get("phone"),
                    bio=row.get("bio"),
                    avatar_path=row.get("avatar_path"),
                    grade=row.get("grade"),
                    experiment_course=row.get("experiment_course"),
                )
            return None
        finally:
            cursor.close()
            conn.close()

    def get_user_by_id(self, user_id: int) -> Optional[User]:
        """通过ID获取用户"""
        conn = self._get_connection()
        cursor = conn.cursor(dictionary=True)
        try:
            cursor.execute("SELECT * FROM users WHERE id = %s", (user_id,))
            row = cursor.fetchone()
            if row:
                return User(
                    id=row["id"],
                    username=row["username"],
                    password_hash=row["password_hash"],
                    created_at=row["created_at"],
                    auto_login=row["auto_login"],
                    nickname=row.get("nickname"),
                    email=row.get("email"),
                    student_id=row.get("student_id"),
                    major=row.get("major"),
                    phone=row.get("phone"),
                    bio=row.get("bio"),
                    avatar_path=row.get("avatar_path"),
                    grade=row.get("grade"),
                    experiment_course=row.get("experiment_course"),
                )
            return None
        finally:
            cursor.close()
            conn.close()

    def update_auto_login(self, user_id: int, auto_login: bool) -> None:
        """更新自动登录设置"""
        conn = self._get_connection()
        cursor = conn.cursor()
        try:
            cursor.execute(
                "UPDATE users SET auto_login = %s WHERE id = %s",
                (auto_login, user_id)
            )
            conn.commit()
        finally:
            cursor.close()
            conn.close()

    def update_last_login(self, user_id: int) -> None:
        """更新最后登录时间"""
        conn = self._get_connection()
        cursor = conn.cursor()
        try:
            cursor.execute(
                "UPDATE users SET last_login = NOW() WHERE id = %s",
                (user_id,)
            )
            conn.commit()
        finally:
            cursor.close()
            conn.close()

    def update_user_profile(self, user_id: int, **fields) -> bool:
        """更新用户个人资料"""
        allowed = {'nickname', 'email', 'student_id', 'major', 'phone', 'bio', 'avatar_path', 'grade', 'experiment_course'}
        valid = {k: v for k, v in fields.items() if k in allowed}
        
        logger.info(f"update_user_profile 调用: user_id={user_id}, fields={list(fields.keys())}, valid={list(valid.keys())}")
        
        if not valid:
            logger.warning("update_user_profile: 没有有效字段")
            return False
        
        set_clause = ", ".join([f"{k} = %s" for k in valid.keys()])
        values = list(valid.values()) + [user_id]
        
        logger.info(f"SQL: UPDATE users SET {set_clause} WHERE id = %s")
        logger.info(f"Values: {values}")
        
        conn = self._get_connection()
        cursor = conn.cursor()
        try:
            sql = f"UPDATE users SET {set_clause} WHERE id = %s"
            cursor.execute(sql, values)
            conn.commit()
            affected = cursor.rowcount
            logger.info(f"更新成功，影响行数: {affected}")
            return True  # <-- 改这里：不管影响几行，没异常就算成功
        except Exception as e:
            logger.error(f"更新用户资料失败: {e}")
            return False
        finally:
            cursor.close()
            conn.close()

    def get_all_usernames(self) -> list[str]:
        """获取所有用户名（用于登录下拉选择）"""
        conn = self._get_connection()
        cursor = conn.cursor(dictionary=True)
        try:
            cursor.execute("SELECT username FROM users ORDER BY last_login DESC")
            return [row["username"] for row in cursor.fetchall()]
        finally:
            cursor.close()
            conn.close()

    def delete_user(self, user_id: int) -> bool:
        """删除用户账号"""
        conn = self._get_connection()
        cursor = conn.cursor()
        try:
            cursor.execute("DELETE FROM users WHERE id = %s", (user_id,))
            conn.commit()
            return cursor.rowcount > 0
        except MySQLError as e:
            logger.error(f"删除用户失败: {e}")
            return False
        finally:
            cursor.close()
            conn.close()

    # ========== 批阅记录管理 ==========

    def save_grading_record(self, user_id: int, filename: str, file_path: str,
                            score_data: Dict, ocr_text: str, sections: Dict,
                            quality_score: Optional[float], style_key: str,
                            style_name: str, is_aborted: bool = False) -> int:
        """保存批阅记录，返回记录ID"""
        conn = self._get_connection()
        cursor = conn.cursor()
        try:
            cursor.execute("""
                INSERT INTO grading_records 
                (user_id, filename, file_path, total_score, total_max, percentage,
                 dimensions, overall_comment, overall_suggestions, strengths, weaknesses,
                 ocr_text, sections, quality_score, style_key, style_name, is_aborted)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (
                user_id, filename, file_path,
                score_data.get("total_score", 0),
                score_data.get("total_max", 100),
                score_data.get("percentage", 0),
                json.dumps(score_data.get("dimensions", {}), ensure_ascii=False),
                score_data.get("overall_comment", ""),
                score_data.get("overall_suggestions", ""),
                json.dumps(score_data.get("strengths", []), ensure_ascii=False),
                json.dumps(score_data.get("weaknesses", []), ensure_ascii=False),
                ocr_text,
                json.dumps(sections, ensure_ascii=False),
                quality_score,
                style_key,
                style_name,
                is_aborted
            ))
            conn.commit()
            record_id = cursor.lastrowid
            logger.info(f"保存批阅记录成功: ID={record_id}, 用户={user_id}, 文件={filename}")
            return record_id
        except MySQLError as e:
            logger.error(f"保存批阅记录失败: {e}")
            return -1
        finally:
            cursor.close()
            conn.close()

    def update_grading_record(self, record_id: int, score_data: Dict, ocr_text: str,
                              sections: Dict, quality_score: Optional[float],
                              style_key: str, style_name: str) -> bool:
        """更新批阅记录（重新批阅覆盖）"""
        conn = self._get_connection()
        cursor = conn.cursor()
        try:
            cursor.execute("""
                UPDATE grading_records SET
                    total_score = %s,
                    total_max = %s,
                    percentage = %s,
                    dimensions = %s,
                    overall_comment = %s,
                    overall_suggestions = %s,
                    strengths = %s,
                    weaknesses = %s,
                    ocr_text = %s,
                    sections = %s,
                    quality_score = %s,
                    style_key = %s,
                    style_name = %s,
                    is_aborted = FALSE,
                    updated_at = NOW()
                WHERE id = %s
            """, (
                score_data.get("total_score", 0),
                score_data.get("total_max", 100),
                score_data.get("percentage", 0),
                json.dumps(score_data.get("dimensions", {}), ensure_ascii=False),
                score_data.get("overall_comment", ""),
                score_data.get("overall_suggestions", ""),
                json.dumps(score_data.get("strengths", []), ensure_ascii=False),
                json.dumps(score_data.get("weaknesses", []), ensure_ascii=False),
                ocr_text,
                json.dumps(sections, ensure_ascii=False),
                quality_score,
                style_key,
                style_name,
                record_id
            ))
            conn.commit()
            logger.info(f"更新批阅记录成功: ID={record_id}")
            return True
        except MySQLError as e:
            logger.error(f"更新批阅记录失败: {e}")
            return False
        finally:
            cursor.close()
            conn.close()

    def get_user_records(self, user_id: int, limit: int = 100, offset: int = 0) -> List[GradingRecord]:
        """获取用户的批阅记录列表"""
        conn = self._get_connection()
        cursor = conn.cursor(dictionary=True)
        try:
            # 先获取该用户所有记录的总数（用于计算 display_id）
            cursor.execute(
                "SELECT COUNT(*) as total FROM grading_records WHERE user_id = %s AND is_aborted = FALSE",
                (user_id,)
            )
            total_count = cursor.fetchone()["total"]
            
            cursor.execute("""
                SELECT * FROM grading_records 
                WHERE user_id = %s AND is_aborted = FALSE
                ORDER BY created_at DESC
                LIMIT %s OFFSET %s
            """, (user_id, limit, offset))
            rows = cursor.fetchall()
            records = [self._row_to_record(row) for row in rows]
            
            # display_id = 总记录数 - 当前记录在全局中的偏移位置
            for i, record in enumerate(records):
                object.__setattr__(record, 'display_id', total_count - offset - i)
                
            return records
        finally:
            cursor.close()
            conn.close()

    def get_record_by_id(self, record_id: int) -> Optional[GradingRecord]:
        """通过ID获取单条记录"""
        conn = self._get_connection()
        cursor = conn.cursor(dictionary=True)
        try:
            cursor.execute("SELECT * FROM grading_records WHERE id = %s", (record_id,))
            row = cursor.fetchone()
            return self._row_to_record(row) if row else None
        finally:
            cursor.close()
            conn.close()

    def get_record_by_filename(self, user_id: int, filename: str) -> Optional[GradingRecord]:
        """通过文件名获取用户最新记录（用于重新批阅时查找）"""
        conn = self._get_connection()
        cursor = conn.cursor(dictionary=True)
        try:
            cursor.execute("""
                SELECT * FROM grading_records 
                WHERE user_id = %s AND filename = %s AND is_aborted = FALSE
                ORDER BY updated_at DESC LIMIT 1
            """, (user_id, filename))
            row = cursor.fetchone()
            return self._row_to_record(row) if row else None
        finally:
            cursor.close()
            conn.close()

    def delete_record(self, record_id: int) -> bool:
        """删除单条记录"""
        conn = self._get_connection()
        cursor = conn.cursor()
        try:
            cursor.execute("DELETE FROM grading_records WHERE id = %s", (record_id,))
            conn.commit()
            return cursor.rowcount > 0
        except MySQLError as e:
            logger.error(f"删除记录失败: {e}")
            return False
        finally:
            cursor.close()
            conn.close()

    def delete_user_records(self, user_id: int) -> bool:
        """删除用户的所有记录"""
        conn = self._get_connection()
        cursor = conn.cursor()
        try:
            cursor.execute("DELETE FROM grading_records WHERE user_id = %s", (user_id,))
            conn.commit()
            return True
        except MySQLError as e:
            logger.error(f"删除用户记录失败: {e}")
            return False
        finally:
            cursor.close()
            conn.close()

    def update_record_fields(self, record_id: int, **fields) -> bool:
        """更新记录的指定字段（用于改动功能），自动更新时间"""
        if not fields:
            logger.warning("update_record_fields: 没有提供任何字段")
            return False
        
        allowed_fields = {
            'filename', 'file_path', 'total_score', 'total_max', 'percentage',
            'dimensions', 'overall_comment', 'overall_suggestions', 
            'strengths', 'weaknesses', 'ocr_text', 'sections', 
            'quality_score', 'style_key', 'style_name'
        }
        
        valid_fields = {k: v for k, v in fields.items() if k in allowed_fields}
        if not valid_fields:
            logger.warning(f"update_record_fields: 没有有效字段，输入字段: {list(fields.keys())}")
            return False
        
        # JSON 字段需要序列化
        json_fields = {'dimensions', 'strengths', 'weaknesses', 'sections'}
        for key in valid_fields:
            if key in json_fields and not isinstance(valid_fields[key], str):
                valid_fields[key] = json.dumps(valid_fields[key], ensure_ascii=False)
        
        # 构建SQL（不再自动更新 updated_at，改为设置 is_modified 标志）
        set_clause = ", ".join([f"{k} = %s" for k in valid_fields.keys()])
        set_clause += ", is_modified = TRUE"
        values = list(valid_fields.values()) + [record_id]
        
        logger.info(f"更新记录 {record_id}，字段: {list(valid_fields.keys())}")
        
        conn = self._get_connection()
        cursor = conn.cursor()
        try:
            sql = f"UPDATE grading_records SET {set_clause} WHERE id = %s"
            logger.debug(f"SQL: {sql}")
            logger.debug(f"Values: {values}")
            cursor.execute(sql, values)
            conn.commit()
            affected = cursor.rowcount
            logger.info(f"更新记录 {record_id} 成功，影响行数: {affected}")
            return affected > 0
        except MySQLError as e:
            logger.error(f"更新记录字段失败: {e}")
            logger.error(f"SQL: UPDATE grading_records SET {set_clause} WHERE id = {record_id}")
            return False
        finally:
            cursor.close()
            conn.close()

    def get_next_display_id(self, user_id: int) -> int:
        """获取该用户的下一个 display_id（用于新增功能）"""
        conn = self._get_connection()
        cursor = conn.cursor(dictionary=True)
        try:
            cursor.execute(
                "SELECT COUNT(*) as total FROM grading_records WHERE user_id = %s AND is_aborted = FALSE",
                (user_id,)
            )
            result = cursor.fetchone()
            return (result["total"] if result else 0) + 1
        finally:
            cursor.close()
            conn.close()

    def get_user_statistics(self, user_id: int) -> Dict[str, Any]:
        """获取用户统计信息"""
        conn = self._get_connection()
        cursor = conn.cursor(dictionary=True)
        try:
            cursor.execute("""
                SELECT 
                    COUNT(*) as total_count,
                    AVG(percentage) as avg_percentage,
                    MAX(percentage) as max_percentage,
                    MIN(percentage) as min_percentage,
                    COUNT(CASE WHEN percentage >= 60 THEN 1 END) as pass_count,
                    COUNT(CASE WHEN percentage >= 85 THEN 1 END) as excellent_count
                FROM grading_records 
                WHERE user_id = %s AND is_aborted = FALSE
            """, (user_id,))
            row = cursor.fetchone()
            return {
                "total_count": row["total_count"] or 0,
                "avg_percentage": float(row["avg_percentage"]) if row["avg_percentage"] else 0,
                "max_percentage": float(row["max_percentage"]) if row["max_percentage"] else 0,
                "min_percentage": float(row["min_percentage"]) if row["min_percentage"] else 0,
                "pass_count": row["pass_count"] or 0,
                "excellent_count": row["excellent_count"] or 0,
            }
        finally:
            cursor.close()
            conn.close()

    def _row_to_record(self, row: Dict) -> GradingRecord:
        """将数据库行转换为GradingRecord对象"""
        return GradingRecord(
            id=row["id"],
            user_id=row["user_id"],
            filename=row["filename"],
            file_path=row["file_path"],
            total_score=row["total_score"],
            total_max=row["total_max"],
            percentage=float(row["percentage"]),
            dimensions=json.loads(row["dimensions"]) if row["dimensions"] else {},
            overall_comment=row["overall_comment"] or "",
            overall_suggestions=row["overall_suggestions"] or "",
            strengths=json.loads(row["strengths"]) if row["strengths"] else [],
            weaknesses=json.loads(row["weaknesses"]) if row["weaknesses"] else [],
            ocr_text=row["ocr_text"] or "",
            sections=json.loads(row["sections"]) if row["sections"] else {},
            quality_score=float(row["quality_score"]) if row["quality_score"] else None,
            style_key=row["style_key"] or "",
            style_name=row["style_name"] or "",
            is_modified=bool(row.get("is_modified", False)),
            created_at=row["created_at"],
            updated_at=row["updated_at"]
        )