# -*- coding: utf-8 -*-
import os
import smtplib
import traceback

def test_debug():
    user = os.environ.get("SMTP_USER", "").strip()
    code = os.environ.get("SMTP_CODE", "").strip()
    print(f"Debug SMTP: user='{user[:3]}***{user[-7:]}', code_len={len(code)}")

    s = smtplib.SMTP_SSL("smtp.qq.com", 465, timeout=20)
    s.set_debuglevel(2)
    try:
        print("--- EHLO ---")
        s.ehlo()
        print("--- LOGIN with user & code ---")
        s.login(user, code)
        print("--- SUCCESS! ---")
    except Exception as e:
        print("--- EXCEPTION ---")
        traceback.print_exc()
    finally:
        try:
            s.close()
        except:
            pass

if __name__ == "__main__":
    test_debug()
