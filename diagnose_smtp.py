# -*- coding: utf-8 -*-
import os
import socket
import ssl
import smtplib
import traceback

def test():
    user = os.environ.get("SMTP_USER", "").strip()
    code = os.environ.get("SMTP_CODE", "").strip()
    to = os.environ.get("SMTP_TO", "").strip() or user
    print(f"SMTP_USER set: {bool(user)}, length={len(user)}")
    print(f"SMTP_CODE set: {bool(code)}, length={len(code)}")

    # 1. DNS check
    print("\n--- DNS Resolution ---")
    try:
        infos = socket.getaddrinfo("smtp.qq.com", 465, socket.AF_UNSPEC, socket.SOCK_STREAM)
        for info in infos:
            print(f"  Family: {info[0]}, Addr: {info[4]}")
    except Exception as e:
        print(f"  DNS failed: {e}")

    # 2. Test port 465 SSL
    print("\n--- Testing port 465 SSL ---")
    for attempt in range(1, 4):
        try:
            print(f"Attempt {attempt} on 465...")
            s = smtplib.SMTP_SSL("smtp.qq.com", 465, timeout=15)
            print("  Connected! Banner:", s.ehlo()[1].decode('utf-8', errors='ignore'))
            if user and code:
                res = s.login(user, code)
                print("  Login success:", res)
            s.quit()
            print("  465 Test SUCCESS!")
            break
        except Exception as e:
            print(f"  465 failed: {type(e).__name__}: {e}")

    # 3. Test port 587 STARTTLS
    print("\n--- Testing port 587 STARTTLS ---")
    for attempt in range(1, 4):
        try:
            print(f"Attempt {attempt} on 587...")
            s = smtplib.SMTP("smtp.qq.com", 587, timeout=15)
            print("  Connected! Banner:", s.ehlo()[1].decode('utf-8', errors='ignore'))
            context = ssl.create_default_context()
            s.starttls(context=context)
            s.ehlo()
            print("  STARTTLS ok!")
            if user and code:
                res = s.login(user, code)
                print("  Login success:", res)
            s.quit()
            print("  587 Test SUCCESS!")
            break
        except Exception as e:
            print(f"  587 failed: {type(e).__name__}: {e}")

if __name__ == "__main__":
    test()
