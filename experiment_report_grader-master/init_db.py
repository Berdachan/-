"""
数据库初始化脚本
运行此脚本创建数据库和用户表
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import mysql.connector  # type: ignore
from config import DB_CONFIG


def init_database():
    """初始化数据库"""
    # 先连接MySQL（不指定数据库）
    config = DB_CONFIG.copy()
    database = config.pop("database")

    try:
        conn = mysql.connector.connect(**config)
        cursor = conn.cursor()

        # 创建数据库
        cursor.execute(
            f"CREATE DATABASE IF NOT EXISTS {database} "
            "CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
        )
        print(f"✅ 数据库 '{database}' 已创建或已存在")

        cursor.close()
        conn.close()

        # 现在连接新创建的数据库，初始化表
        from modules.database import DatabaseManager  # type: ignore
        DatabaseManager()  # __init__ 会自动初始化表
        print("✅ 数据表初始化完成")
        print("✅ 数据库准备就绪！")

    except mysql.connector.Error as e:
        err_code = e.errno if hasattr(e, 'errno') else 0
        # 1007 = database exists, 忽略此错误继续
        if err_code == 1007:
            print(f"✅ 数据库 '{database}' 已存在")
            try:
                from modules.database import DatabaseManager  # type: ignore
                DatabaseManager()
                print("✅ 数据表初始化完成")
                print("✅ 数据库准备就绪！")
                return
            except Exception as e2:
                print(f"❌ 数据表初始化失败: {e2}")
                sys.exit(1)
        print(f"❌ 数据库初始化失败: {e}")
        print("\n请检查:")
        print("1. MySQL服务是否已启动")
        print("2. config.py 中的 DB_CONFIG 配置是否正确")
        print("3. 用户名和密码是否正确")
        sys.exit(1)


if __name__ == "__main__":
    print("=" * 50)
    print("  实验报告批阅系统 - 数据库初始化")
    print("=" * 50)
    init_database()