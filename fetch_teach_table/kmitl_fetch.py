"""
kmitl_fetch.py
===============

ขั้นที่ 2 ของ pipeline: ใช้ token + cookie ที่ login_kmitl.py เก็บไว้
ยิงขอข้อมูลตารางวิชา (teach-table-show) วนหลายปี/เทอม/คณะ
โดยไม่ต้อง login ใหม่ทุกครั้ง

มี logic เช็คว่า token ใกล้หมดอายุหรือโดน 401 แล้วพยายาม refresh
ด้วย refresh_token (ถ้า Keycloak realm นี้รองรับ grant_type=refresh_token)

วิธีใช้:
    python kmitl_fetch.py
"""

import json
import time
from pathlib import Path

import requests

STATE_FILE = Path(__file__).parent / "session_state.json"
API_BASE = "https://regis.reg.kmitl.ac.th/api/"

# TODO: ยืนยัน path จริงของ token endpoint จาก DevTools (ตอนนี้เดาจาก pattern ที่เห็น)
# เช่น https://sso.reg.kmitl.ac.th/realms/<realm>/protocol/openid-connect/token
TOKEN_ENDPOINT = "https://sso.reg.kmitl.ac.th/realms/registrar/protocol/openid-connect/token"
CLIENT_ID = "KMITL-client"  # เห็นจาก auth?client_id=KMITL-client ใน network log


def load_state() -> dict:
    if not STATE_FILE.exists():
        raise FileNotFoundError(
            f"ไม่พบ {STATE_FILE} — กรุณารัน kmitl_login.py ก่อนเพื่อ login และสร้างไฟล์นี้"
        )
    return json.loads(STATE_FILE.read_text())


def save_state(state: dict):
    STATE_FILE.write_text(json.dumps(state, indent=2, ensure_ascii=False))


def build_session(state: dict) -> requests.Session:
    session = requests.Session()
    for c in state.get("cookies", []):
        session.cookies.set(c["name"], c["value"], domain=c.get("domain"))
    return session


def refresh_token_if_needed(state: dict, session: requests.Session) -> dict:
    """เช็คว่า token ใกล้หมดอายุหรือยัง ถ้าใกล้ ลอง refresh ก่อนยิง API จริง"""
    captured_at = state.get("captured_at", 0)
    expires_in = state.get("expires_in", 0)
    age = time.time() - captured_at

    # ถ้าใช้ไปแล้วเกิน 80% ของเวลาหมดอายุ ให้ลอง refresh ก่อน
    if expires_in and age > expires_in * 0.8 and state.get("refresh_token"):
        print("[i] token ใกล้หมดอายุ กำลังลอง refresh...")
        try:
            resp = session.post(
                TOKEN_ENDPOINT,
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": state["refresh_token"],
                    "client_id": CLIENT_ID,
                },
                timeout=10,
            )
            if resp.status_code == 200:
                body = resp.json()
                state["access_token"] = body["access_token"]
                state["refresh_token"] = body.get("refresh_token", state["refresh_token"])
                state["expires_in"] = body.get("expires_in")
                state["captured_at"] = time.time()
                save_state(state)
                print("[+] refresh token สำเร็จ")
            else:
                print(f"[!] refresh token ไม่สำเร็จ (status {resp.status_code}) "
                      f"— อาจต้อง login ใหม่ด้วย kmitl_login.py")
        except Exception as e:
            print(f"[!] refresh token error: {e} — อาจต้อง login ใหม่")

    return state


def fetch_teach_table(session: requests.Session, token: str, *, year: int, semester: int,
                       faculty: str, department: str, curriculum: str = "x",
                       class_year: int = 0, search_all_curriculum: bool = True,
                       search_all_class_year: bool = True) -> dict | None:
    headers = {"Authorization": f"Bearer {token}"}
    params = {
        "function": "get-teach-table-show",
        "mode": "by_class",
        "selected_year": year,
        "selected_semester": semester,
        "selected_faculty": faculty,
        "selected_department": department,
        "selected_curriculum": curriculum,
        "selected_class_year": class_year,
        "search_all_faculty": "false",
        "search_all_department": "false",
        "search_all_curriculum": str(search_all_curriculum).lower(),
        "search_all_class_year": str(search_all_class_year).lower(),
    }

    resp = session.get(API_BASE, headers=headers, params=params, timeout=30)

    if resp.status_code == 401:
        print(f"[!] โดน 401 (token ใช้ไม่ได้แล้ว) ที่ year={year} semester={semester}")
        return None

    resp.raise_for_status()
    return resp.json()


def main():
    state = load_state()
    session = build_session(state)

    # ตัวอย่าง: วน fetch หลายเทอม/หลายคณะ ปรับ list นี้ตามที่ต้องการจริง
    jobs = [
        {"year": y, "semester": s, "faculty": "07", "department": "01"}
        for y in [2566, 2567, 2568, 2569]
        for s in [1, 2, 3]
    ]

    results = []
    for job in jobs:
        state = refresh_token_if_needed(state, session)
        token = state["access_token"]

        print(f"[i] กำลังดึงข้อมูล: {job}")
        data = fetch_teach_table(session, token, **job)

        if data is None:
            print("[!] หยุดเพราะ token ใช้ไม่ได้ — กรุณารัน kmitl_login.py ใหม่")
            break

        results.append({"params": job, "data": data})
        time.sleep(1)  # เว้นจังหวะกันโดน rate-limit / bot detection

    out_path = Path(__file__).parent / "teach_table_results.json"
    out_path.write_text(json.dumps(results, indent=2, ensure_ascii=False))
    print(f"[+] เสร็จแล้ว บันทึกผลลัพธ์ {len(results)} รายการไปที่ {out_path}")


if __name__ == "__main__":
    main()
