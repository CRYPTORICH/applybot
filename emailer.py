"""
ApplyBot Emailer — sends confirmation emails for every application submitted.
Uses SMTP via Gmail (already configured in himalaya).
"""
import smtplib
import os
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime

# Gmail SMTP config — ALL CREDENTIALS FROM ENV, NEVER HARDCODED
SMTP_SERVER = "smtp.gmail.com"
SMTP_PORT = 587
SMTP_USER = os.environ.get("GMAIL_ADDRESS", "")
SMTP_PASS = os.environ.get("GMAIL_APP_PASSWORD", "")

FROM_NAME = "ApplyBot"
FROM_EMAIL = SMTP_USER


def send_application_confirmation(to_email: str, job_title: str, company: str,
                                   platform: str, url: str, app_id: str) -> bool:
    """Send confirmation email for a single job application."""
    if not SMTP_PASS:
        print("[emailer] No GMAIL_APP_PASSWORD set — skipping email")
        return False

    subject = f"✓ Applied: {job_title} at {company}"
    
    body = f"""Hi,

ApplyBot just submitted an application on your behalf:

    📌 Position: {job_title}
    🏢 Company: {company}
    🌐 Platform: {platform}
    🔗 Listing: {url}
    🕐 Time: {datetime.now().strftime('%B %d, %Y at %I:%M %p ET')}
    📋 App ID: {app_id}

This is an automated confirmation. You'll receive one for every application we submit.

━━━━━━━━━━━━━━━━━━━━
🎁 Share ApplyBot with a friend and you BOTH get 2 free applications!
Your referral link: https://applybot-yavz.onrender.com/?ref={app_id[:8]}
━━━━━━━━━━━━━━━━━━━━

Need to track everything? View your dashboard at:
https://applybot-yavz.onrender.com/dashboard/{app_id[:36]}

— ApplyBot
"""

    msg = MIMEMultipart()
    msg["From"] = f"{FROM_NAME} <{FROM_EMAIL}>"
    msg["To"] = to_email
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain"))

    try:
        server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT)
        server.starttls()
        server.login(SMTP_USER, SMTP_PASS)
        server.sendmail(FROM_EMAIL, to_email, msg.as_string())
        server.quit()
        print(f"[emailer] ✓ Sent confirmation to {to_email}: {job_title} @ {company}")
        return True
    except Exception as e:
        print(f"[emailer] ✗ Failed to send to {to_email}: {e}")
        return False


def send_welcome_email(to_email: str, user_id: str, tokens: int) -> bool:
    """Send welcome email after purchase."""
    if not SMTP_PASS:
        return False

    subject = f"Welcome to ApplyBot — {tokens} applications ready"
    
    body = f"""Hi,

Thanks for choosing ApplyBot! You have {tokens} application tokens ready to use.

👉 Get started: https://applybot-yavz.onrender.com/apply/{user_id}

Here's what happens next:
1. Upload your resume at the link above
2. Tell us what kind of jobs you want
3. We search public listings and apply automatically
4. You get an email confirmation for every application

Questions? Reply to this email.

— ApplyBot
"""

    msg = MIMEMultipart()
    msg["From"] = f"{FROM_NAME} <{FROM_EMAIL}>"
    msg["To"] = to_email
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain"))

    try:
        server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT)
        server.starttls()
        server.login(SMTP_USER, SMTP_PASS)
        server.sendmail(FROM_EMAIL, to_email, msg.as_string())
        server.quit()
        print(f"[emailer] ✓ Welcome email sent to {to_email}")
        return True
    except Exception as e:
        print(f"[emailer] ✗ Welcome email failed: {e}")
        return False


def send_daily_summary(to_email: str, apps_submitted: int, apps_total: int) -> bool:
    """Send daily summary of applications."""
    if not SMTP_PASS:
        return False

    subject = f"ApplyBot Daily Summary — {apps_submitted} applications today"
    
    body = f"""Hi,

Here's your ApplyBot activity for {datetime.now().strftime('%B %d, %Y')}:

    📤 Applications submitted today: {apps_submitted}
    📊 Total applications: {apps_total}

View your live dashboard:
https://applybot-yavz.onrender.com

— ApplyBot
"""

    msg = MIMEMultipart()
    msg["From"] = f"{FROM_NAME} <{FROM_EMAIL}>"
    msg["To"] = to_email
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain"))

    try:
        server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT)
        server.starttls()
        server.login(SMTP_USER, SMTP_PASS)
        server.sendmail(FROM_EMAIL, to_email, msg.as_string())
        server.quit()
        print(f"[emailer] ✓ Daily summary sent to {to_email}")
        return True
    except Exception as e:
        print(f"[emailer] ✗ Summary failed: {e}")
        return False
