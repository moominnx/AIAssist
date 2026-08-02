"""
build_chatbot_db.py
-------------------
สร้าง SQLite DB สำหรับ chatbot ตอบคำถามนักศึกษา (hybrid)

  - แผนการเรียน (academic_plan_courses)  <-- มาจาก Ground Truth (GT)  [มี coop/no_coop ครบ verify แล้ว]
  - ส่วนที่เหลือ (courses, prerequisites, curriculum_structure, career,
    outcomes, tracks, meta)             <-- มาจาก Qwen extraction

เหตุผล: GT มีแผน coop + no_coop ครบและถูกต้อง (qwen ดึงมาไม่ครบ/ไม่มี coop ของ IT,DSBA)
        chatbot ควรใช้ข้อมูลที่ verify แล้วสำหรับแผนการเรียน

Usage:
    python3 build_chatbot_db.py <extracted_teach_table_dir> <gt_dir> <output_db>
Example:
    python3 build_chatbot_db.py ./data/extracted_teach_table ./data/gt ./chatbot_teach_table.db
"""

import json
import re
import sqlite3
import sys
from pathlib import Path

PROGRAMS = ["IT", "DSBA", "BIT", "AIT"]
CREDITS_RE = re.compile(r"^(\d+)\((\d+|x)-(\d+|x)-(\d+|x)\)$")
CREDITS_TOTAL_RE = re.compile(r"^(\d+)")


def parse_credits(raw):
    if not raw:
        return None, None, None, None
    m = CREDITS_RE.match(str(raw).strip())
    if m:
        total, lec, lab, self_ = m.groups()
        to_int = lambda v: int(v) if v.isdigit() else None
        return int(total), to_int(lec), to_int(lab), to_int(self_)
    m2 = CREDITS_TOTAL_RE.match(str(raw).strip())
    return (int(m2.group(1)) if m2 else None), None, None, None


def to_int(v):
    """แปลงเป็น int; ถ้าไม่ได้คืน None. '0' -> 0 (วิชาเลือกแบบยืดหยุ่น)"""
    try:
        return int(str(v).strip())
    except (ValueError, TypeError):
        return None


SCHEMA = """
CREATE TABLE programs (
    program_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    program_code    TEXT UNIQUE NOT NULL,
    name_th TEXT, name_en TEXT, degree_th TEXT, degree_en TEXT,
    major_th TEXT, major_en TEXT, total_credits INTEGER, program_type TEXT,
    duration_years INTEGER, curriculum_year TEXT, institution TEXT, faculty TEXT
);

CREATE TABLE curriculum_structure (
    structure_id INTEGER PRIMARY KEY AUTOINCREMENT,
    program_id INTEGER NOT NULL REFERENCES programs(program_id),
    category TEXT NOT NULL, credits INTEGER, description TEXT
);
CREATE TABLE curriculum_subcategories (
    subcategory_id INTEGER PRIMARY KEY AUTOINCREMENT,
    structure_id INTEGER NOT NULL REFERENCES curriculum_structure(structure_id),
    subcategory_text TEXT NOT NULL
);

CREATE TABLE courses (
    course_id INTEGER PRIMARY KEY AUTOINCREMENT,
    program_id INTEGER NOT NULL REFERENCES programs(program_id),
    code TEXT NOT NULL, name_th TEXT, name_en TEXT,
    credits_raw TEXT, credits_total INTEGER,
    lecture_hours INTEGER, lab_hours INTEGER, self_study_hours INTEGER,
    category TEXT, type TEXT, year INTEGER, semester INTEGER
);
CREATE INDEX idx_courses_program_code ON courses(program_id, code);

CREATE TABLE course_prerequisites (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    course_id INTEGER NOT NULL REFERENCES courses(course_id),
    prerequisite_code TEXT NOT NULL
);

-- แผนการเรียนจาก GT: ตารางแบน 1 แถว = 1 วิชาในแผน (query ง่ายสำหรับ Text-to-SQL)
-- plan_type = 'coop' หรือ 'no_coop'  (AIT มีแค่ 'no_coop' เพราะไม่มีสหกิจ)
-- year/semester = 0 หมายถึงวิชาเลือกแบบยืดหยุ่น (ดู flexible_year_semester ประกอบ)
CREATE TABLE academic_plan_courses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    program_id INTEGER NOT NULL REFERENCES programs(program_id),
    plan_type TEXT NOT NULL,
    code TEXT, name_th TEXT, name_en TEXT,
    credits_raw TEXT, credits_total INTEGER,
    year INTEGER, semester INTEGER,
    flexible_year_semester TEXT,
    category TEXT, type TEXT, prerequisite TEXT, note TEXT,
    module TEXT,   -- NULL = วิชา core เรียนทุก module; มีค่า = เฉพาะ module นั้น
    matched_course_id INTEGER REFERENCES courses(course_id)
);
CREATE INDEX idx_plan_prog_type ON academic_plan_courses(program_id, plan_type);

CREATE TABLE career_opportunities (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    program_id INTEGER NOT NULL REFERENCES programs(program_id),
    career_text TEXT NOT NULL
);
CREATE TABLE program_outcomes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    program_id INTEGER NOT NULL REFERENCES programs(program_id),
    plo_code TEXT, description TEXT
);
CREATE TABLE special_tracks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    program_id INTEGER NOT NULL REFERENCES programs(program_id),
    track_text TEXT NOT NULL
);
CREATE TABLE extraction_meta (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    program_id INTEGER NOT NULL REFERENCES programs(program_id),
    model TEXT DEFAULT 'qwen', source_files TEXT,
    part1_success INTEGER, part2_success INTEGER
);
"""


def load_qwen_parts(cur, program_id, data):
    """โหลดทุกอย่างจาก qwen ยกเว้น academic_plan. คืน dict code->course_id."""
    info = data["program_info"]
    cur.execute("""UPDATE programs SET name_th=?,name_en=?,degree_th=?,degree_en=?,
        major_th=?,major_en=?,total_credits=?,program_type=?,duration_years=?,
        curriculum_year=?,institution=?,faculty=? WHERE program_id=?""",
        (info.get("name_th"), info.get("name_en"), info.get("degree_th"), info.get("degree_en"),
         info.get("major_th"), info.get("major_en"), info.get("total_credits"),
         info.get("program_type"), info.get("duration_years"), info.get("curriculum_year"),
         info.get("institution"), info.get("faculty"), program_id))

    for category, struct in data.get("curriculum_structure", {}).items():
        cur.execute("INSERT INTO curriculum_structure (program_id,category,credits,description) VALUES (?,?,?,?)",
                    (program_id, category, struct.get("credits"), struct.get("description")))
        sid = cur.lastrowid
        for sub in struct.get("subcategories", []) or []:
            cur.execute("INSERT INTO curriculum_subcategories (structure_id,subcategory_text) VALUES (?,?)", (sid, sub))

    code_to_id = {}
    for c in data.get("courses", []):
        tot, lec, lab, ss = parse_credits(c.get("credits"))
        cur.execute("""INSERT INTO courses (program_id,code,name_th,name_en,credits_raw,
            credits_total,lecture_hours,lab_hours,self_study_hours,category,type,year,semester)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (program_id, c.get("code"), c.get("name_th"), c.get("name_en"), c.get("credits"),
             tot, lec, lab, ss, c.get("category"), c.get("type"), c.get("year"), c.get("semester")))
        cid = cur.lastrowid
        code_to_id[c.get("code")] = cid
        for pr in c.get("prerequisites", []) or []:
            cur.execute("INSERT INTO course_prerequisites (course_id,prerequisite_code) VALUES (?,?)", (cid, pr))

    for career in data.get("career_opportunities", []):
        cur.execute("INSERT INTO career_opportunities (program_id,career_text) VALUES (?,?)", (program_id, career))
    for outcome in data.get("program_outcomes", []):
        m = re.match(r"^\s*(PLO\s*\d+)\s*[:：]\s*(.*)$", outcome)
        code, desc = (m.group(1), m.group(2)) if m else (None, outcome)
        cur.execute("INSERT INTO program_outcomes (program_id,plo_code,description) VALUES (?,?,?)", (program_id, code, desc))
    for track in data.get("special_tracks", []):
        cur.execute("INSERT INTO special_tracks (program_id,track_text) VALUES (?,?)", (program_id, track))

    meta = data.get("_extract_meta", {})
    cur.execute("""INSERT INTO extraction_meta (program_id,model,source_files,part1_success,part2_success)
        VALUES (?,?,?,?,?)""",
        (program_id, "qwen", json.dumps(meta.get("source_files", []), ensure_ascii=False),
         int(bool(meta.get("part1_success"))), int(bool(meta.get("part2_success")))))
    return code_to_id


def load_gt_plan(cur, program_id, code_to_id, gt_file, plan_type):
    """โหลดแผนการเรียนจากไฟล์ GT หนึ่งไฟล์ ลง academic_plan_courses"""
    with open(gt_file, encoding="utf-8") as f:
        data = json.load(f)
    n = 0
    for c in data.get("courses", []):
        tot, _, _, _ = parse_credits(c.get("credits"))
        cur.execute("""INSERT INTO academic_plan_courses
            (program_id,plan_type,code,name_th,name_en,credits_raw,credits_total,
             year,semester,flexible_year_semester,category,type,prerequisite,note,matched_course_id)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (program_id, plan_type, c.get("code"), c.get("name_th"), c.get("name_en"),
             c.get("credits"), tot, to_int(c.get("year")), to_int(c.get("semester")),
             c.get("flexible_year_semester"), c.get("category"), c.get("type"),
             c.get("prerequisite"), c.get("note"), code_to_id.get(c.get("code"))))
        n += 1
    return n


# แผนที่ module ต่อโปรแกรม
# IT: ดูจาก note ในแผน (วิชา module เป็นบังคับ ติด note กลุ่มวิชา)
IT_MODULE_MAP = [
    ("%การพัฒนาซอฟต์แวร์%", "software"),
    ("%Full-Stack%",        "software"),
    ("%โครงสร้างพื้นฐาน%",   "network"),
    ("%Network/System%",    "network"),
    ("%สื่อประสม%",         "game"),
    ("%Game Developer%",    "game"),
]
# DSBA: ดูจาก category ของวิชา (track อยู่ใน course master ไม่ได้อยู่ใน note ของแผน)
DSBA_MODULE_MAP = [
    ("%วิทยาการข้อมูล%",        "data_science"),
    ("%วิศวกรรมข้อมูล%",        "data_engineering"),
    ("%การวิเคราะห์เชิงสถิติ%", "statistical_analysis"),
]


def derive_modules(cur):
    """เติมค่า module ลง academic_plan_courses (NULL = วิชา core เรียนทุก module)
    IT ดึงจาก note ของแผน, DSBA ดึงจาก category ของวิชาที่ match. BIT/AIT ไม่มี module."""
    it_id = cur.execute("SELECT program_id FROM programs WHERE program_code='IT'").fetchone()
    dsba_id = cur.execute("SELECT program_id FROM programs WHERE program_code='DSBA'").fetchone()

    if it_id:
        for pat, mod in IT_MODULE_MAP:
            cur.execute(
                "UPDATE academic_plan_courses SET module=? "
                "WHERE program_id=? AND module IS NULL AND note LIKE ?",
                (mod, it_id[0], pat))
    if dsba_id:
        for pat, mod in DSBA_MODULE_MAP:
            cur.execute(
                "UPDATE academic_plan_courses SET module=? "
                "WHERE program_id=? AND module IS NULL AND matched_course_id IN "
                "(SELECT course_id FROM courses WHERE category LIKE ?)",
                (mod, dsba_id[0], pat))


def main():
    if len(sys.argv) != 4:
        print(__doc__); sys.exit(1)
    src_dir, gt_dir, out_path = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
    if out_path.exists():
        out_path.unlink()

    conn = sqlite3.connect(out_path)
    cur = conn.cursor()
    cur.executescript(SCHEMA)

    for prog in PROGRAMS:
        cur.execute("INSERT INTO programs (program_code) VALUES (?)", (prog,))
        pid = cur.lastrowid

        # 1) qwen parts
        qpath = src_dir / prog / f"{prog}_qwen.json"
        with open(qpath, encoding="utf-8") as f:
            code_to_id = load_qwen_parts(cur, pid, json.load(f))

        # 2) GT academic plans
        gt_prog = gt_dir / prog
        plan_files = {
            "coop": gt_prog / f"{prog}_academic_plan_coop.json",
            "no_coop": gt_prog / f"{prog}_academic_plan_no_coop.json",
            "single": gt_prog / f"{prog}_academic_plan.json",   # AIT
        }
        loaded = []
        if plan_files["coop"].exists() and plan_files["no_coop"].exists():
            loaded.append(("coop", load_gt_plan(cur, pid, code_to_id, plan_files["coop"], "coop")))
            loaded.append(("no_coop", load_gt_plan(cur, pid, code_to_id, plan_files["no_coop"], "no_coop")))
        elif plan_files["single"].exists():
            # AIT มีแผนเดียว สหกิจบังคับที่ 4/2 ไม่มีตัวเลือก coop/no_coop -> ป้าย 'standard'
            loaded.append(("standard", load_gt_plan(cur, pid, code_to_id, plan_files["single"], "standard")))
        else:
            print(f"[WARN] {prog}: ไม่พบไฟล์ GT academic plan")
        summary = ", ".join(f"{t}={n}" for t, n in loaded)
        print(f"[OK] {prog}: courses={len(code_to_id)} | GT plan {summary}")

    conn.commit()

    derive_modules(cur)
    conn.commit()

    print("\n--- สรุปตาราง ---")
    for t in ["programs", "courses", "academic_plan_courses", "career_opportunities"]:
        n = cur.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        print(f"  {t}: {n}")
    print("\n--- academic_plan_courses แยกตาม program + plan_type ---")
    for r in cur.execute("""SELECT p.program_code, apc.plan_type, COUNT(*) n
        FROM academic_plan_courses apc JOIN programs p ON p.program_id=apc.program_id
        GROUP BY p.program_code, apc.plan_type ORDER BY p.program_code, apc.plan_type"""):
        print(f"  {r[0]:5} {r[1]:8} {r[2]} วิชา")

    conn.close()
    print(f"\nDone -> {out_path}")


if __name__ == "__main__":
    main()