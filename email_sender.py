# -*- coding: utf-8 -*-
"""
邮件发送模块（支持附件版）。

特性：
1. 支持 HTML 正文 + 多个附件（CSV / JSON 等）
2. 自动重试（默认 3 次），每次间隔递增
3. 支持 Gmail / 163 双端口降级：587 STARTTLS → 465 SSL
4. 针对不同异常给出明确提示
5. 密码自动去掉空格（Gmail 应用密码常见坑）
6. 失败时抛出异常，确保 GitHub Actions 明确标红
7. 显式写入北京时间 Date 头，避免 163 等邮箱客户端时区解析错误
"""
import os
import time
import smtplib
import ssl
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email import encoders
from email.header import Header
from datetime import datetime, timezone, timedelta

BJT = timezone(timedelta(hours=8))


def _require_env(name: str) -> str:
    val = os.environ.get(name)
    if not val:
        raise RuntimeError(f"[email] 缺少必要环境变量：{name}")
    return val


def _build_message(subject, html, smtp_user, recipient, attachments=None):
    """
    构建邮件对象。
    attachments: list of (filename, content_bytes, mime_type)
        例: [("trades.csv", b"a,b,c", "text/csv")]
    """
    # 顶层用 mixed，内部嵌 alternative（HTML 正文）
    msg = MIMEMultipart('mixed')
    alt = MIMEMultipart('alternative')
    alt.attach(MIMEText(html, 'html', 'utf-8'))
    msg.attach(alt)

    msg['Subject'] = Header(subject.replace('\n', ' ').replace('\r', '').strip(), 'utf-8')
    msg['From'] = smtp_user
    msg['To'] = recipient
    msg['Date'] = datetime.now(BJT).strftime('%a, %d %b %Y %H:%M:%S +0800')

    # 添加附件
    if attachments:
        for filename, content, mime_type in attachments:
            try:
                maintype, subtype = mime_type.split('/', 1)
                part = MIMEBase(maintype, subtype)
                part.set_payload(content)
                encoders.encode_base64(part)
                # 用 utf-8 编码文件名，兼容中文
                part.add_header(
                    'Content-Disposition', 'attachment',
                    filename=('utf-8', '', filename)
                )
                msg.attach(part)
            except Exception as e:
                print(f"[email] ⚠️ 附件 {filename} 添加失败：{e}")

    return msg


def _send_once(host, port, use_ssl, user, password, recipient, msg, timeout=60):
    """单次尝试发送。失败抛异常，成功正常返回。"""
    if use_ssl:
        context = ssl.create_default_context()
        with smtplib.SMTP_SSL(host, port, timeout=timeout, context=context) as server:
            server.login(user, password)
            server.sendmail(user, [recipient], msg.as_string())
    else:
        context = ssl.create_default_context()
        with smtplib.SMTP(host, port, timeout=timeout) as server:
            server.ehlo()
            server.starttls(context=context)
            server.ehlo()
            server.login(user, password)
            server.sendmail(user, [recipient], msg.as_string())


def send_html_email(subject: str, html: str, attachments=None, max_retries: int = 3) -> None:
    """
    发送 HTML 邮件（可带附件）。
    attachments: list of (filename, content_bytes, mime_type)
    """
    smtp_server = _require_env("SMTP_SERVER")
    smtp_port = int(_require_env("SMTP_PORT"))
    smtp_user = _require_env("SMTP_USERNAME")
    smtp_pass = _require_env("SMTP_PASSWORD").replace(" ", "")
    recipient = _require_env("RECIPIENT_EMAIL")

    msg = _build_message(subject, html, smtp_user, recipient, attachments=attachments)

    # 端口候选：优先用户配置的端口，另一个作为降级
    if smtp_port == 465:
        port_candidates = [(465, True), (587, False)]
    else:
        port_candidates = [(587, False), (465, True)]

    last_error = None

    for attempt in range(1, max_retries + 1):
        for port, use_ssl in port_candidates:
            label = "SSL" if use_ssl else "STARTTLS"
            try:
                print(f"[email] 第 {attempt}/{max_retries} 次尝试：{smtp_server}:{port}（{label}）")
                _send_once(smtp_server, port, use_ssl,
                           smtp_user, smtp_pass, recipient, msg)
                att_note = f"（附件 {len(attachments)} 个）" if attachments else ""
                print(f"[email] ✅ 发送成功 → {recipient}{att_note}")
                return
            except smtplib.SMTPAuthenticationError as e:
                print("[email] ❌ 认证失败（535）。")
                print("[email] 原因：Gmail 主密码重置后，应用专用密码被自动撤销。")
                print("[email] 解决：")
                print("[email]   1. 打开 https://myaccount.google.com/apppasswords")
                print("[email]   2. 生成新的 16 位应用专用密码")
                print("[email]   3. 更新 GitHub Secrets 中的 SMTP_PASSWORD（不带空格）")
                print(f"[email] 原始错误：{e}")
                raise
            except (smtplib.SMTPServerDisconnected,
                    smtplib.SMTPConnectError,
                    TimeoutError,
                    OSError) as e:
                print(f"[email] ⚠️  {smtp_server}:{port} 网络异常：{e}")
                last_error = e
                continue
            except smtplib.SMTPRecipientsRefused as e:
                print(f"[email] ❌ 收件人被拒绝（检查 RECIPIENT_EMAIL）：{e}")
                raise
            except smtplib.SMTPException as e:
                print(f"[email] ⚠️  {smtp_server}:{port} SMTP 异常：{e}")
                last_error = e
                continue

        if attempt < max_retries:
            wait = 2 ** attempt
            print(f"[email] 本轮所有端口均失败，等待 {wait}s 后重试...")
            time.sleep(wait)

    print(f"[email] ❌ 已重试 {max_retries} 轮，全部失败。最后一次错误：{last_error}")
    raise RuntimeError(f"邮件发送失败（已重试 {max_retries} 轮）：{last_error}")
