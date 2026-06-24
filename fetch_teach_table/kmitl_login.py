"""
kmitl_login.py
===============

ขั้นที่ 1 ของ pipeline: เปิดเบราว์เซอร์จริง (ผ่าน Playwright) ให้ผู้ใช้ login
ผ่านระบบ SSO ของ KMITL (Keycloak) ด้วยตัวเอง (รองรับ 2FA / captcha ถ้ามี)

ระหว่างนั้น script จะ "ดักฟัง" (intercept) request ที่ระบบยิงไปขอ token
แล้วดึง access_token / refresh_token / expires_in ออกมา พร้อมเก็บคุกกี้
ทั้งหมดที่เบราว์เซอร์ได้รับ (รวมคุกกี้ของ Incapsula ที่ใช้กันบอท)

ผลลัพธ์: ไฟล์ session_state.json ที่มีทุกอย่างที่ kmitl_fetch.py ต้องใช้ต่อ

วิธีใช้:
    pip install playwright --break-system-packages
    playwright install chromium
    python kmitl_login.py

หมายเหตุ: รันครั้งนี้ "เปิดเบราว์เซอร์ให้เห็นจอ" (headless=False) เพราะต้องให้
คนกรอก username/password เอง ไม่ได้ใส่ credential ไว้ในโค้ด (เพื่อความปลอดภัย)
"""

import json
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

START_URL = "https://regis.reg.kmitl.ac.th/#/teach_table_selector"
STATE_FILE = Path(__file__).parent / "session_state.json"

# คำที่ใช้จับ request ที่เป็นการขอ/ใช้ token (ปรับได้ถ้าเจอ path จริงต่างจากนี้)
TOKEN_URL_HINTS = ["/protocol/openid-connect/token", "token"]


def looks_like_token_response(url: str) -> bool:
    return any(hint in url for hint in TOKEN_URL_HINTS)


def main():
    captured = {"access_token": None, "refresh_token": None, "expires_in": None,
                "captured_at": None, "token_endpoint": None}

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)  # เปิดให้เห็นจอ ต้อง login เอง
        context = browser.new_context()
        page = context.new_page()

        def handle_response(response):
            try:
                if response.request.method == "POST" and looks_like_token_response(response.url):
                    body = response.json()
                    if "access_token" in body:
                        captured["access_token"] = body.get("access_token")
                        captured["refresh_token"] = body.get("refresh_token")
                        captured["expires_in"] = body.get("expires_in")
                        captured["captured_at"] = time.time()
                        captured["token_endpoint"] = response.url
                        print(f"[+] จับ token ได้จาก: {response.url}")
                        print(f"    expires_in = {body.get('expires_in')} วินาที")
            except Exception:
                # response บางตัวไม่ใช่ JSON หรือไม่เกี่ยวข้อง ข้ามไปเฉยๆ
                pass

        page.on("response", handle_response)

        print("=" * 60)
        print("เปิดเบราว์เซอร์แล้ว กรุณา login ด้วยตัวเองในหน้าที่เปิดขึ้นมา")
        print("(กรอก username / password / ผ่าน 2FA ถ้ามี ตามปกติ)")
        print("สคริปต์จะรอจนกว่าจะจับ token ได้ หรือครบเวลาที่กำหนด")
        print("=" * 60)

        page.goto(START_URL)

        # รอจนกว่าจะจับ access_token ได้ หรือ timeout (ปรับเวลาได้ตามต้องการ)
        max_wait_seconds = 180
        waited = 0
        while captured["access_token"] is None and waited < max_wait_seconds:
            page.wait_for_timeout(1000)
            waited += 1

        if captured["access_token"] is None:
            print("[!] ไม่จับ token ได้ภายในเวลาที่กำหนด")
            print("    ลองตรวจสอบ TOKEN_URL_HINTS ด้านบนว่าตรงกับ path จริงหรือไม่")
            print("    (เปิด DevTools ดู request ชื่อ 'token' อีกครั้งเพื่อยืนยัน path)")
        else:
            # เก็บคุกกี้ทั้งหมดที่เบราว์เซอร์มีตอนนี้ (รวม Incapsula cookies)
            cookies = context.cookies()
            captured["cookies"] = cookies

            STATE_FILE.write_text(json.dumps(captured, indent=2, ensure_ascii=False))
            print(f"[+] บันทึก session ลงไฟล์แล้ว: {STATE_FILE}")
            print(f"[+] จำนวนคุกกี้ที่เก็บไว้: {len(cookies)}")

        browser.close()


if __name__ == "__main__":
    main()
