import os, requests
def send(text):
    sent = False
    topic = os.environ.get("NTFY_TOPIC")
    if topic:
        first, _, rest = text.partition("\n")
        requests.post(f"https://ntfy.sh/{topic}", data=rest.strip().encode(), headers={"Title": first.encode(), "Priority": "high"}, timeout=20).raise_for_status(); sent = True
    to, user, pw = os.environ.get("MAIL_TO"), os.environ.get("GMAIL_USER"), os.environ.get("GMAIL_APP_PASSWORD")
    if to and user and pw:
        import smtplib; from email.message import EmailMessage
        msg = EmailMessage(); msg["From"], msg["To"], msg["Subject"] = user, to, text.split("\n", 1)[0]; msg.set_content(text)
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s: s.login(user, pw); s.send_message(msg)
        sent = True
    if not sent: print("（未設通知渠道）\n" + text)
