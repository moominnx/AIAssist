# Login & Fetch Flow — ระบบทะเบียน KMITL (regis.reg.kmitl.ac.th)

สรุปสำหรับนำเสนออาจารย์: เราพบว่าระบบนี้มีการป้องกัน **2 ชั้น** ที่ไม่เกี่ยวกับ
"การเปลี่ยนเทอม" เลย และมีวิธีจัดการที่ชัดเจนแล้ว

## สิ่งที่ตรวจพบจาก DevTools

| ชั้น | ชื่อ | หน้าที่ | อายุการใช้งาน |
|---|---|---|---|
| 1 | JWT Access Token (Keycloak SSO) | ยืนยันตัวตนผู้ใช้ ส่งใน header `Authorization: Bearer ...` | สั้น (นาที-ชั่วโมง) ต้อง refresh |
| 2 | Incapsula Cookie (`visid_incap`, `incap_ses`) | ป้องกันบอท/scraper ของเว็บไซต์ | rotate เป็นระยะ ไม่เกี่ยวกับ login |

**ข้อค้นพบสำคัญ:** พารามิเตอร์ปี/เทอม/คณะ/หลักสูตร (`selected_year`,
`selected_semester`, ...) ถูกส่งเป็น **query string ตรงๆ** ไม่ได้ถูกเก็บไว้ใน
session ฝั่ง server เลย ดังนั้นการเปลี่ยนเทอมจึง**ไม่ทำให้ token หรือ cookie
เสียหรือต้อง login ใหม่** — เพียงแค่เปลี่ยนค่า parameter ในคำขอเดิม

## API endpoint ที่ใช้ดึงข้อมูลตารางวิชา

```
GET https://regis.reg.kmitl.ac.th/api/?function=get-teach-table-show
    &mode=by_class
    &selected_year=2569
    &selected_semester=1
    &selected_faculty=07
    &selected_department=01
    &selected_curriculum=x
    &selected_class_year=0
    &search_all_curriculum=true
    &search_all_class_year=true
Headers: Authorization: Bearer <access_token>
Cookies: (cookies ของ Incapsula ที่ได้ตอน login)
```

## Flow การทำงาน (2 ไฟล์)

```
┌─────────────────────┐         ┌──────────────────────────┐
│  kmitl_login.py      │         │  kmitl_fetch.py            │
│  (รันครั้งเดียว)        │  --->   │  (รันซ้ำได้หลายครั้ง)         │
│                      │         │                            │
│  1. เปิดเบราว์เซอร์      │         │  1. โหลด token+cookie       │
│  2. คนกรอก login เอง   │         │  2. เช็คว่า token ใกล้หมด     │
│  3. ดักจับ token       │ session │     อายุหรือยัง -> refresh   │
│     +cookie จาก        │ _state  │  3. วน loop ทุกปี/เทอม/คณะ   │
│     network response   │ .json   │     ที่ต้องการ ยิง API       │
│  4. เซฟลง JSON          │         │  4. เซฟผลลัพธ์เป็น JSON      │
└─────────────────────┘         └──────────────────────────┘
```

- `kmitl_login.py` — เปิดเบราว์เซอร์จริงผ่าน Playwright ให้ผู้ใช้ login ด้วย
  ตัวเอง (รองรับ 2FA/captcha) แล้วดักฟัง network request ที่คืน `access_token`
  / `refresh_token` พร้อมเก็บคุกกี้ทั้งหมด บันทึกลง `session_state.json`
- `kmitl_fetch.py` — โหลด `session_state.json` มาใช้ยิง API ดึงตารางวิชา
  วนหลายปี/เทอม/คณะได้โดยไม่ต้อง login ใหม่ มี logic refresh token อัตโนมัติ
  เมื่อใกล้หมดอายุ หรือแจ้งเตือนเมื่อโดน 401

## สิ่งที่ยังต้องตรวจสอบเพิ่ม (ก่อนใช้งานจริง)

1. **Token endpoint ที่แท้จริง** — ในโค้ดเดาไว้เป็น
   `https://sso.reg.kmitl.ac.th/realms/registrar/protocol/openid-connect/token`
   ต้องเปิด DevTools คลิก request ชื่อ `token` แล้วดู Request URL เต็มๆ
   เพื่อยืนยัน (ตอนแคปภาพ URL ถูกตัดไว้)
2. **Refresh token grant ใช้ได้จริงหรือไม่** — Keycloak บาง realm ปิด
   refresh token ไว้ ถ้าปิด จะต้อง login ใหม่ทั้ง flow ทุกครั้งที่หมดอายุ
   (ยังใช้งานได้ แค่ไม่สะดวกเท่า)
3. **Rate limit / bot detection ของ Incapsula** — ถ้ายิง request ถี่เกินไป
   อาจโดนบล็อก ควรมี delay ระหว่าง request (ในโค้ดมี `time.sleep(1)` ไว้แล้ว
   ปรับเพิ่มได้ถ้าโดนบล็อก)

## วิธีรัน

```bash
pip install playwright requests --break-system-packages
playwright install chromium

python kmitl_login.py     # login ครั้งเดียว ได้ session_state.json
python kmitl_fetch.py     # ดึงข้อมูลตามรายการปี/เทอมที่กำหนดใน jobs[]
```
