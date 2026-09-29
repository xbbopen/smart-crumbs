import os, smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.header import Header

def send_html_email(subject, html):
    subject = subject.replace('\n',' ').replace('\r','').strip()
    smtp_server = os.environ.get("SMTP_SERVER", "smtp.gmail.com")
    smtp_port = int(os.environ.get("SMTP_PORT", "587"))
    smtp_user = os.environ.get("SMTP_USERNAME")
    smtp_pass = os.environ.get("SMTP_PASSWORD")
    recipient = os.environ.get("RECIPIENT_EMAIL")

    msg = MIMEMultipart('alternative')
    msg['Subject'] = Header(subject, 'utf-8')
    msg['From'] = smtp_user
    msg['To'] = recipient
    msg.attach(MIMEText(html, 'html', 'utf-8'))

    try:
        server = smtplib.SMTP(smtp_server, smtp_port)
        server.starttls()
        server.login(smtp_user, smtp_pass)
        server.sendmail(smtp_user, [recipient], msg.as_string())
        server.quit()
        print("[email] 发送成功")
    except smtplib.SMTPAuthenticationError as e:
        print(f"[email] ❌ 认证失败（535）— 请检查 SMTP_PASSWORD 是否为最新的应用专用密码: {e}")
        raise  # 抛出异常让 GitHub Actions 标记为失败
    except smtplib.SMTPException as e:
        print(f"[email] ❌ SMTP 错误: {e}")
        raise
    except Exception as e:
        print(f"[email] ❌ 发送失败: {e}")
        raise
