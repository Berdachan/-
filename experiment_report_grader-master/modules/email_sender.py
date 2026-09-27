"""
邮件发送模块
负责：发送批阅结果（Excel/PDF）到指定邮箱
"""

import smtplib
import logging
import json
from pathlib import Path
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
from typing import Optional

from config import CACHE_DIR

logger = logging.getLogger(__name__)


class EmailSender:
    """邮件发送器"""

    def __init__(self, user_id: Optional[int] = None):
        self.user_id = user_id
        self.smtp_config = self._load_smtp_config()

    def _load_smtp_config(self) -> dict:
        if self.user_id is None:
            return {}
        config_file = CACHE_DIR / f"smtp_config_{self.user_id}.json"
        if config_file.exists():
            try:
                with open(config_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception as e:
                logger.warning(f"加载SMTP配置失败: {e}")
        return {}

    def _save_smtp_config(self):
        if self.user_id is None:
            return
        try:
            config_file = CACHE_DIR / f"smtp_config_{self.user_id}.json"
            config_file.parent.mkdir(parents=True, exist_ok=True)
            with open(config_file, 'w', encoding='utf-8') as f:
                json.dump(self.smtp_config, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.warning(f"保存SMTP配置失败: {e}")

    def configure(self, smtp_server: str, smtp_port: int,
                  sender_email: str, sender_password: str,
                  use_ssl: bool = True) -> bool:
        self.smtp_config = {
            "server": smtp_server,
            "port": smtp_port,
            "sender": sender_email,
            "password": sender_password,
            "use_ssl": use_ssl,
        }
        self._save_smtp_config()
        logger.info(f"SMTP配置已保存: {sender_email}@{smtp_server}")
        return True

    def test_connection(self) -> tuple[bool, str]:
        if not self.smtp_config:
            return False, "SMTP未配置"

        try:
            server = self.smtp_config["server"]
            port = self.smtp_config["port"]
            sender = self.smtp_config["sender"]
            password = self.smtp_config["password"]
            use_ssl = self.smtp_config.get("use_ssl", True)

            if use_ssl:
                with smtplib.SMTP_SSL(server, port, timeout=10) as smtp:
                    smtp.login(sender, password)
            else:
                with smtplib.SMTP(server, port, timeout=10) as smtp:
                    smtp.starttls()
                    smtp.login(sender, password)

            return True, "连接成功"
        except Exception as e:
            return False, str(e)

    def send_grading_result(self, recipient_email: str, recipient_name: str,
                            attachment_path: Path, subject: Optional[str] = None,
                            message_body: Optional[str] = None) -> tuple[bool, str]:
        if not self.smtp_config:
            return False, "SMTP未配置，请先配置邮箱"

        if not attachment_path.exists():
            return False, f"附件不存在: {attachment_path}"

        try:
            server = self.smtp_config["server"]
            port = self.smtp_config["port"]
            sender = self.smtp_config["sender"]
            password = self.smtp_config["password"]
            use_ssl = self.smtp_config.get("use_ssl", True)

            msg = MIMEMultipart()
            msg['From'] = sender
            msg['To'] = recipient_email
            msg['Subject'] = subject or f"实验报告批阅结果 - {recipient_name}"

            body = message_body or f"""尊敬的{recipient_name}老师/同学：

您好！

附件为您本次实验报告的批阅结果，请查收。

如有疑问，欢迎随时联系。

祝好！
实验报告批阅智能体
"""
            msg.attach(MIMEText(body, 'plain', 'utf-8'))

            with open(attachment_path, 'rb') as f:
                part = MIMEBase('application', 'octet-stream')
                part.set_payload(f.read())
            encoders.encode_base64(part)
            filename = attachment_path.name
            part.add_header(
                'Content-Disposition',
                f'attachment; filename="{filename}"'
            )
            msg.attach(part)

            if use_ssl:
                with smtplib.SMTP_SSL(server, port, timeout=30) as smtp:
                    smtp.login(sender, password)
                    smtp.send_message(msg)
            else:
                with smtplib.SMTP(server, port, timeout=30) as smtp:
                    smtp.starttls()
                    smtp.login(sender, password)
                    smtp.send_message(msg)

            logger.info(f"邮件已发送至 {recipient_email}")
            return True, f"邮件已发送至 {recipient_email}"

        except Exception as e:
            logger.error(f"发送邮件失败: {e}")
            return False, f"发送失败: {e}"

    def send_bulk(self, recipients: list[dict], attachment_path: Path) -> dict:
        results = {"success": [], "failed": []}

        for recipient in recipients:
            success, msg = self.send_grading_result(
                recipient_email=recipient["email"],
                recipient_name=recipient.get("name", recipient["email"]),
                attachment_path=attachment_path
            )
            if success:
                results["success"].append({"email": recipient["email"], "message": msg})
            else:
                results["failed"].append({"email": recipient["email"], "error": msg})

        return results

    def get_config(self) -> dict:
        config = self.smtp_config.copy()
        if "password" in config:
            config["password"] = "*" * len(config["password"])
        return config

    def is_configured(self) -> bool:
        return bool(self.smtp_config and self.smtp_config.get("server"))