import os, smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.header import Header

def send_html_email(subject, html):
    # 修复 550 错误：清除换行符
    subject = subject.replace('\n', ' ').replace('\r', '').strip()
    smtp_server = os.environ.get("SMTP_SERVER", "smtp.gmail.com")
    smtp_port = int(os.environ.get("SMTP_PORT", "587"))
    smtp_username = os.environ.get("SMTP_USERNAME")
    smtp_password = os.environ.get("SMTP_PASSWORD")
    recipient = os.environ.get("RECIPIENT_EMAIL")

    msg = MIMEMultipart('alternative')
    msg['Subject'] = Header(subject, 'utf-8')
    msg['From'] = smtp_username
    msg['To'] = recipient
    msg.attach(MIMEText(html, 'html', 'utf-8'))

    try:
        server = smtplib.SMTP(smtp_server, smtp_port)
        server.starttls()
        server.login(smtp_username, smtp_password)
        server.sendmail(smtp_username, [recipient], msg.as_string())
        server.quit()
        print("[email] 邮件发送成功")
    except Exception as e:
        print(f"[email] 邮件发送失败: {e}")
