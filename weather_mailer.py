"""Fetch Bern, Switzerland weather from Open-Meteo and email it once via Gmail.

Usage (from venv):
    pip install -r requirements.txt
    copy .env.example .env   # then fill in real values
    python weather_mailer.py --dry-run   # fetch + print only, no email
    python weather_mailer.py             # fetch + send email once
"""

from __future__ import annotations

import argparse
import os
import smtplib
import sys
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from dotenv import load_dotenv

# Bern, Switzerland. timezone=Asia/Kolkata so hourly slots come back in IST.
API_URL = (
    "https://api.open-meteo.com/v1/forecast"
    "?latitude=46.9481&longitude=7.4474"
    "&hourly=temperature_2m&forecast_days=1&timezone=Asia%2FKolkata"
)
IST = ZoneInfo("Asia/Kolkata")


def fetch_weather() -> dict:
    resp = requests.get(API_URL, timeout=20)
    resp.raise_for_status()
    data = resp.json()
    times: list[str] = data["hourly"]["time"]
    temps: list[float] = data["hourly"]["temperature_2m"]
    unit: str = data["hourly_units"]["temperature_2m"]
    if not times or not temps or len(times) != len(temps):
        raise ValueError("Unexpected Open-Meteo response shape")
    return {"times": times, "temps": temps, "unit": unit}


def build_summary(weather: dict) -> tuple[str, str, str]:
    times, temps, unit = weather["times"], weather["temps"], weather["unit"]
    now_ist = datetime.now(IST)
    today = now_ist.date().isoformat()

    current = temps[0]
    hi, lo = max(temps), min(temps)
    hi_t = times[temps.index(hi)]
    lo_t = times[temps.index(lo)]
    # 8 AM IST slot, fallback to first slot
    slot_8am = next((t for t in temps if times[temps.index(t)].endswith("T08:00")), temps[8])

    subject = f"Bern weather {today}: {current}{unit} now, {lo}{unit}–{hi}{unit}"
    lines = [
        f"Bern, Switzerland — {today} (IST view, source: Open-Meteo)",
        f"Now: {current}{unit} | High: {hi}{unit} at {hi_t} | Low: {lo}{unit} at {lo_t}",
        f"8 AM IST: {slot_8am}{unit}",
        "",
        "Hourly:",
        *[f"  {t[11:]}  {v}{unit}" for t, v in zip(times, temps)],
    ]
    text = "\n".join(lines)

    def card(label: str, value: str, sub: str = "") -> str:
        return (
            f'<td align="center" style="padding:12px 8px;background:#ffffff;'
            f'border:1px solid #dadce0;border-radius:8px;width:25%;">'
            f'<div style="font-size:11px;color:#5f6368;text-transform:uppercase;'
            f'letter-spacing:0.5px;">{label}</div>'
            f'<div style="font-size:22px;color:#202124;font-weight:bold;'
            f'margin:4px 0;">{value}</div>'
            f'<div style="font-size:11px;color:#5f6368;">{sub}</div></td>'
        )

    rows = []
    for t, v in zip(times, temps):
        hhmm = t[11:]
        hl = 'background:#e8f0fe;font-weight:bold;' if hhmm == "08:00" else ""
        tag = ""
        if v == hi:
            tag = ' <span style="color:#d93025;">▲ high</span>'
        elif v == lo:
            tag = ' <span style="color:#1a73e8;">▼ low</span>'
        rows.append(
            f'<tr><td style="padding:8px 12px;border-bottom:1px solid #eef0f2;{hl}">'
            f"{hhmm}</td>"
            f'<td align="right" style="padding:8px 12px;border-bottom:1px solid #eef0f2;{hl}">'
            f"{v}{unit}{tag}</td></tr>"
        )

    html = f"""<div style="font-family:Arial,Helvetica,sans-serif;background:#f1f3f4;padding:24px 12px;">
  <div style="max-width:600px;margin:0 auto;background:#ffffff;border-radius:12px;overflow:hidden;border:1px solid #dadce0;">
    <div style="background:#1a73e8;padding:20px 24px;">
      <div style="font-size:20px;color:#ffffff;font-weight:bold;">&#9728; Bern, Switzerland</div>
      <div style="font-size:13px;color:#d2e3fc;">{today} &middot; IST view &middot; Open-Meteo</div>
    </div>
    <div style="padding:16px;">
      <table role="presentation" width="100%" cellpadding="0" cellspacing="8" style="border-collapse:separate;">
        <tr>
          {card("Now", f"{current}{unit}", "midnight IST slot")}
          {card("High", f"{hi}{unit}", hi_t[11:])}
          {card("Low", f"{lo}{unit}", lo_t[11:])}
          {card("8 AM", f"{slot_8am}{unit}", "IST")}
        </tr>
      </table>
      <div style="font-size:14px;color:#202124;font-weight:bold;margin:12px 4px 8px;">Hourly forecast</div>
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse;font-size:14px;color:#202124;">
        {''.join(rows)}
      </table>
      <div style="font-size:11px;color:#80868b;margin-top:12px;">Source: Open-Meteo (46.9481, 7.4474) &middot; hourly temperature_2m</div>
    </div>
  </div>
</div>"""
    return subject, text, html


def send_email(subject: str, text: str, html: str) -> None:
    import re

    user = os.environ.get("GMAIL_USER", "").strip().strip("'\"")
    raw_pw = os.environ.get("GMAIL_APP_PASSWORD", "").strip().strip("'\"")
    app_pw = re.sub(r"\s+", "", raw_pw)  # app passwords show as "abcd efgh ..." — remove ALL whitespace
    to = os.environ.get("TO_EMAIL", user).strip().strip("'\"")
    if not user or not app_pw:
        sys.exit("Missing GMAIL_USER / GMAIL_APP_PASSWORD. Copy .env.example to .env and fill them.")
    if not to:
        sys.exit("Missing TO_EMAIL (defaults to GMAIL_USER).")
    print(f"Logging in as {user} (app-pw length {len(app_pw)}, expected 16)...")

    msg = EmailMessage()
    msg["From"] = user
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(text)
    msg.add_alternative(html, subtype="html")

    try:
        with smtplib.SMTP("smtp.gmail.com", 587, timeout=30) as s:
            s.ehlo()
            s.starttls()
            s.ehlo()
            s.login(user, app_pw)
            s.send_message(msg)
    except smtplib.SMTPAuthenticationError:
        sys.exit(
            "Gmail rejected login (535). Check:\n"
            " 1. GMAIL_USER is the SAME account that created the app password\n"
            " 2. 2-Step Verification is ON, and you used an App password (16 letters), not your normal password\n"
            " 3. If you regenerated it, update .env — old ones stop working\n"
            " 4. No extra quotes/spaces in .env (spaces inside the password are OK, script strips them)"
        )
    print(f"Sent to {to}: {subject}")


def main() -> None:
    load_dotenv()
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="fetch + print, do not send email")
    args = ap.parse_args()

    weather = fetch_weather()
    subject, text, html = build_summary(weather)
    print(subject)
    print(text)
    if args.dry_run:
        Path("preview.html").write_text(html, encoding="utf-8")
        print("Wrote preview.html — open it in a browser to see the Gmail layout.")
    else:
        send_email(subject, text, html)


if __name__ == "__main__":
    main()
