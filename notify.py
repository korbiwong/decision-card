import os, requests
def send(text):
    """通知失敗唔會令成個掃描失敗。"""
    sent = False
    topic = (os.environ.get("NTFY_TOPIC") or "").strip()
    if topic:
        try:
            first, _, rest = text.partition("\n")
            r = requests.post(f"https://ntfy.sh/{topic}", data=rest.strip().encode(), headers={"Title": first.encode(), "Priority": "high"}, timeout=20)
            print("ntfy", r.status_code); sent = r.ok
        except Exception as e:
            print("ntfy failed:", e)
    to, user, pw = os.environ.get("MAIL_TO"), os.environ.get("GMAIL_USER"), os.environ.get("GMAIL_APP_PASSWORD")
    if to and user and pw:
        try:
            import smtplib; from email.message import EmailMessage
            msg = EmailMessage(); msg["From"], msg["To"], msg["Subject"] = user, to, text.split("\n", 1)[0]; msg.set_content(text)
            with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s: s.login(user, pw); s.send_message(msg)
            sent = True
        except Exception as e:
            print("mail failed:", e)
    if not sent: print("（通知未送出）\n" + text)
