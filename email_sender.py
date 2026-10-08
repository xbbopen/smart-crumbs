# -*- coding: utf-8 -*-
"""
邮件发送模块（增强版）。

改进点：
1. 加入自动重试（默认 3 次），每次间隔递增。
2. 支持 Gmail 双端口降级：587 STARTTLS → 465 SSL。
3. 针对不同异常给出明确提示（网络超时 / 535 认证 / 收件人被拒）。
4. 密码自动去掉空格（Gmail 应用密码复制常见坑）。
5. SMTP_PORT 增加容错，无效时回退 587。
6. 失败时抛出异常，确保 GitHub Actions 明确标红。
"""
import os
import time
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


def _build_message(subject: str, html: str, smtp_user: str, recipient: str) -> MIMEMultipart:
    msg = MIMEMultipart('alternative')
    msg['Subject'] = Header(subject.replace('\n', ' ').replace('\r', '').strip(), 'utf-8')
    msg['From'] = smtp_user
    msg['To'] = recipient
    msg.attach(MIMEText(html, 'html', 'utf-8'))
    return msg


def _send_once(host: str, port: int, use_ssl: bool,
               user: str, password: str, recipient: str,
               msg: MIMEMultipart, timeout: int = 30) -> None:
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


def send_html_email(subject: str, html: str, max_retries: int = 3) -> None:
    """
    发送 HTML 邮件。
    策略：
      - 每轮先尝试 587（STARTTLS），失败切 465（SSL）。
      - 整个流程最多循环 max_retries 次，每次间隔递增（2s / 4s / 8s）。
      - 若遇 535 认证失败，直接终止重试（密码问题重试无用）。
    """
    smtp_server = _require_env("SMTP_SERVER")
    smtp_port_str = _require_env("SMTP_PORT")
    try:
        smtp_port = int(smtp_port_str)
    except ValueError:
        smtp_port = 587          # 修复：无效端口回退默认值
    smtp_user = _require_env("SMTP_USERNAME")
    smtp_pass = _require_env("SMTP_PASSWORD").replace(" ", "")
    recipient = _require_env("RECIPIENT_EMAIL")

    msg = _build_message(subject, html, smtp_user, recipient)

    # 端口候选：优先用户配置的端口，另一个作为降级
    port_candidates = []
    if smtp_port == 465:
        port_candidates = [(465, True), (587, False)]
    else:
        port_candidates = [(587, False), (465, True)]

    last_error: Exception | None = None

    for attempt in range(1, max_retries + 1):
        for port, use_ssl in port_candidates:
            label = "SSL" if use_ssl else "STARTTLS"
            try:
                print(f"[email] 第 {attempt}/{max_retries} 次尝试：{smtp_server}:{port}（{label}）")
                _send_once(smtp_server, port, use_ssl,
                           smtp_user, smtp_pass, recipient, msg)
                print(f"[email] ✅ 发送成功 → {recipient}")
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
