# -*- coding: utf-8 -*-
import os
import smtplib
import traceback

def test_login_variations():
    user = os.environ.get("SMTP_USER", "").strip()
    code = os.environ.get("SMTP_CODE", "").strip()

    variations = [
        ("raw_user", user),
        ("pure_digits", user.split("@")[0]),
    ]
    code_clean = code.replace(" ", "").strip()

    for label, u in variations:
        print(f"\n--- Trying username variation: {label} ({u}) ---")
        try:
            s = smtplib.SMTP_SSL("smtp.qq.com", 465, timeout=15)
            s.ehlo()
            # Try AUTH LOGIN specifically instead of AUTH PLAIN
            print(f"Logging in with user={u}...")
            s.login(u, code_clean)
            print(f"🎉 SUCCESS! Login worked with {label}!")
            s.quit()
            return label, u
        except Exception as e:
            print(f"Failed with {label}: {e}")

if __name__ == "__main__":
    test_login_variations()
