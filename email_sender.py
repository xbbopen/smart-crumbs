# -*- coding: utf-8 -*-
"""
email_sender：使用 Python 标准库 smtplib + email.mime 发送 HTML 邮件。

从环境变量读取：
  SMTP_SERVER, SMTP_PORT, SMTP_USERNAME, SMTP_PASSWORD, RECIPIENT_EMAIL

默认使用 Gmail SMTP：smtp.gmail.com:587，TLS 加密。
"""
from __future__ import annotations

import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def send_html_email(subject: str, html_body: str) -> bool:
    """发送 HTML 邮件。成功返回 True，失败记录并返回 False。"""
    server = _env("SMTP_SERVER", "smtp.gmail.com")
    port = int(_env("SMTP_PORT", "587"))
    username = _env("SMTP_USERNAME")
    password = _env("SMTP_PASSWORD")
    recipient = _env("RECIPIENT_EMAIL")

    if not (username and password and recipient):
        print("[email] 缺少 SMTP_USERNAME / SMTP_PASSWORD / RECIPIENT_EMAIL 环境变量，跳过发送")
        return False

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = username
    msg["To"] = recipient
    # 必须显式编码，避免中文主题乱码
    msg["Subject"] = str(subject)
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    try:
        with smtplib.SMTP(server, port, timeout=30) as smtp:
            smtp.ehlo()
            smtp.starttls()
            smtp.ehlo()
            smtp.login(username, password)
            smtp.sendmail(username, [recipient], msg.as_string())
        print(f"[email] 已发送邮件至 {recipient}")
        return True
    except Exception as e:  # noqa: BLE001
        print(f"[email] 邮件发送失败: {e}")
        return False


if __name__ == "__main__":
    # 手动测试：python email_sender.py
    send_html_email("测试邮件", "<h1>发送成功</h1>")