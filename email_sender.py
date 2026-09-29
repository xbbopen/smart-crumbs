# -*- coding: utf-8 -*-
"""
邮件发送模块。

重要：
- Gmail 在重置主密码后会自动撤销所有"应用专用密码"，
  此时 SMTP 登录会返回 535 认证失败。
- 本模块在失败时**抛出异常**，确保 GitHub Actions 能捕捉到并标红，
  避免出现"邮件没发出去但 job 显示成功"的情况。
"""
import os
import smtplib
import ssl
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.header import Header


def _require_env(name: str) -> str:
    val = os.environ.get(name)
    if not val:
        raise RuntimeError(f"[email] 缺少必要环境变量：{name}")
    return val


def send_html_email(subject: str, html: str) -> None:
    """
    发送 HTML 邮件。
    成功：正常返回；失败：抛出异常（并打印明确原因）。
    """
    # 标题去掉换行，避免 Header 报错
    subject = subject.replace('\n', ' ').replace('\r', '').strip()

    smtp_server = _require_env("SMTP_SERVER")
    smtp_port = int(_require_env("SMTP_PORT"))
    smtp_user = _require_env("SMTP_USERNAME")
    smtp_pass = _require_env("SMTP_PASSWORD")
    recipient = _require_env("RECIPIENT_EMAIL")

    # Gmail 应用专用密码是 16 位，复制时经常带空格，这里兜底清理
    smtp_pass = smtp_pass.replace(" ", "")

    msg = MIMEMultipart('alternative')
    msg['Subject'] = Header(subject, 'utf-8')
    msg['From'] = smtp_user
    msg['To'] = recipient
    msg.attach(MIMEText(html, 'html', 'utf-8'))

    context = ssl.create_default_context()

    try:
        with smtplib.SMTP(smtp_server, smtp_port, timeout=30) as server:
            server.ehlo()
            server.starttls(context=context)
            server.ehlo()
            server.login(smtp_user, smtp_pass)
            server.sendmail(smtp_user, [recipient], msg.as_string())
        print(f"[email] ✅ 发送成功 → {recipient}")

    except smtplib.SMTPAuthenticationError as e:
        # Gmail 密码重置后最常见的错误
        print("[email] ❌ 认证失败（535）。")
        print("[email] 常见原因：Gmail 主密码重置后，应用专用密码被 Google 自动撤销。")
        print("[email] 解决：")
        print("[email]   1. 打开 https://myaccount.google.com/apppasswords")
        print("[email]   2. 生成新的 16 位应用专用密码")
        print("[email]   3. 更新 GitHub Secrets 中的 SMTP_PASSWORD（不带空格）")
        print(f"[email] 原始错误：{e}")
        raise

    except smtplib.SMTPRecipientsRefused as e:
        print(f"[email] ❌ 收件人被拒绝（检查 RECIPIENT_EMAIL）：{e}")
        raise

    except smtplib.SMTPServerDisconnected as e:
        print(f"[email] ❌ SMTP 服务器主动断开（网络或端口问题）：{e}")
        raise

    except smtplib.SMTPException as e:
        print(f"[email] ❌ SMTP 异常：{e}")
        raise

    except OSError as e:
        print(f"[email] ❌ 网络/IO 异常：{e}")
        raise

    except Exception as e:
        print(f"[email] ❌ 未知异常：{e}")
        raise
