"""
build_qwen_db.py
------------------
Build a SQLite relational database from UniAssist's Qwen curriculum-extraction
JSON files (extracted_teach_table/<PROGRAM>/<PROGRAM>_qwen.json).

Usage:
    python3 build_qwen_db.py <path_to_extracted_teach_table_dir> <output_db_path>

Example:
    python3 build_qwen_db.py ./data/extracted_teach_table ./qwen_teach_table.db
"""

import json
import re
import sqlite3
import sys
from pathlib import Path

PROGRAMS = ["IT", "DSBA", "BIT", "AIT"]

# credits string pattern e.g. "3(3-0-6)" -> total=3, lecture=3, lab=0, self_study=6
CREDITS_RE = re.compile(r"^(\d+)\((\d+|x)-(\d+|x)-(\d+|x)\)$")
CREDITS_TOTAL_RE = re.compile(r"^(\d+)")


def parse_credits(raw: str):
    """Return (total_credits, lecture_hours, lab_hours, self_study_hours).
    Numeric fields are None when unparseable (e.g. 'x' placeholders or
    compound values like '3(3-0-6) หรือ 3(2-2-5)')."""
    if not raw:
        return None, None, None, None
    m = CREDITS_RE.match(raw.strip())
    if m:
        total, lec, lab, self_ = m.groups()
        to_int = lambda v: int(v) if v.isdigit() else None
        return int(total), to_int(lec), to_int(lab), to_int(self_)
    # fallback: at least grab the leading total-credit number
    m2 = CREDITS_TOTAL_RE.match(raw.strip())
    total = int(m2.group(1)) if m2 else None
    return total, None, None, None


SCHEMA = """
CREATE TABLE programs (
    program_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    program_code    TEXT UNIQUE NOT NULL,   -- IT / DSBA / BIT / AIT
    name_th         TEXT,
    name_en         TEXT,
    degree_th       TEXT,
    degree_en       TEXT,
    major_th        TEXT,
    major_en        TEXT,
    total_credits   INTEGER,
    program_type    TEXT,
    duration_years  INTEGER,
    curriculum_year TEXT,
    institution     TEXT,
    faculty         TEXT
);

CREATE TABLE curriculum_structure (
    structure_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    program_id      INTEGER NOT NULL REFERENCES programs(program_id),
    category        TEXT NOT NULL,   -- general_education / specific_courses / elective_courses / free_electives
    credits         INTEGER,
    description     TEXT
);

CREATE TABLE curriculum_subcategories (
    subcategory_id  INTEGER PRIMARY KEY AUTOINCREMENT,
    structure_id    INTEGER NOT NULL REFERENCES curriculum_structure(structure_id),
    subcategory_text TEXT NOT NULL
);

CREATE TABLE courses (
    course_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    program_id      INTEGER NOT NULL REFERENCES programs(program_id),
    code            TEXT NOT NULL,        -- not globally unique (AIT has placeholder codes)
    name_th         TEXT,
    name_en         TEXT,
    credits_raw     TEXT,
    credits_total   INTEGER,
    lecture_hours   INTEGER,
    lab_hours       INTEGER,
    self_study_hours INTEGER,
    category        TEXT,
    type            TEXT,               -- บังคับ / เลือก
    year            INTEGER,
    semester        INTEGER
);
CREATE INDEX idx_courses_program_code ON courses(program_id, code);

CREATE TABLE course_prerequisites (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    course_id       INTEGER NOT NULL REFERENCES courses(course_id),
    prerequisite_code TEXT NOT NULL
);

CREATE TABLE academic_plans (
    plan_id         INTEGER PRIMARY KEY AUTOINCREMENT,
    program_id      INTEGER NOT NULL REFERENCES programs(program_id),
    year            INTEGER,
    semester        INTEGER,
    plan_type       TEXT,             -- ปกติ / สหกิจ
    total_credits   INTEGER,
    derived         INTEGER DEFAULT 0 -- 1 = สร้างขึ้นเองจาก heuristic (ดู build_derived_coop_plan),
                                       -- ไม่ได้มาจากผล extract ของ qwen ตรงๆ
);

CREATE TABLE academic_plan_courses (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    plan_id         INTEGER NOT NULL REFERENCES academic_plans(plan_id),
    code            TEXT,
    name_en         TEXT,
    credits_raw     TEXT,
    credits_total   INTEGER,
    lecture_hours   INTEGER,
    lab_hours       INTEGER,
    self_study_hours INTEGER,
    matched_course_id INTEGER REFERENCES courses(course_id)  -- best-effort link, NULL if no match
);

CREATE TABLE career_opportunities (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    program_id      INTEGER NOT NULL REFERENCES programs(program_id),
    career_text     TEXT NOT NULL
);

CREATE TABLE program_outcomes (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    program_id      INTEGER NOT NULL REFERENCES programs(program_id),
    plo_code        TEXT,             -- e.g. 'PLO 1'
    description     TEXT
);

CREATE TABLE special_tracks (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    program_id      INTEGER NOT NULL REFERENCES programs(program_id),
    track_text      TEXT NOT NULL
);

CREATE TABLE extraction_meta (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    program_id              INTEGER NOT NULL REFERENCES programs(program_id),
    model                   TEXT DEFAULT 'qwen',
    source_files            TEXT,   -- JSON-encoded list
    part1_success           INTEGER,
    part2_success           INTEGER,
    prompt_tokens_part1     INTEGER,
    completion_tokens_part1 INTEGER,
    cost_usd_part1          REAL,
    prompt_tokens_part2     INTEGER,
    completion_tokens_part2 INTEGER,
    cost_usd_part2          REAL
);
"""


def load_program(cur, program_code: str, data: dict):
    info = data["program_info"]
    cur.execute(
        """INSERT INTO programs
           (program_code, name_th, name_en, degree_th, degree_en, major_th, major_en,
            total_credits, program_type, duration_years, curriculum_year, institution, faculty)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            program_code,
            info.get("name_th"), info.get("name_en"),
            info.get("degree_th"), info.get("degree_en"),
            info.get("major_th"), info.get("major_en"),
            info.get("total_credits"), info.get("program_type"),
            info.get("duration_years"), info.get("curriculum_year"),
            info.get("institution"), info.get("faculty"),
        ),
    )
    program_id = cur.lastrowid

    # curriculum_structure + subcategories
    for category, struct in data.get("curriculum_structure", {}).items():
        cur.execute(
            "INSERT INTO curriculum_structure (program_id, category, credits, description) VALUES (?,?,?,?)",
            (program_id, category, struct.get("credits"), struct.get("description")),
        )
        structure_id = cur.lastrowid
        for sub in struct.get("subcategories", []) or []:
            cur.execute(
                "INSERT INTO curriculum_subcategories (structure_id, subcategory_text) VALUES (?,?)",
                (structure_id, sub),
            )

    # courses + prerequisites
    code_to_course_id = {}  # last-seen course_id per code (for best-effort plan linking)
    for c in data.get("courses", []):
        total, lec, lab, self_ = parse_credits(c.get("credits"))
        cur.execute(
            """INSERT INTO courses
               (program_id, code, name_th, name_en, credits_raw, credits_total,
                lecture_hours, lab_hours, self_study_hours, category, type, year, semester)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                program_id, c.get("code"), c.get("name_th"), c.get("name_en"),
                c.get("credits"), total, lec, lab, self_,
                c.get("category"), c.get("type"), c.get("year"), c.get("semester"),
            ),
        )
        course_id = cur.lastrowid
        code_to_course_id[c.get("code")] = course_id
        for prereq_code in c.get("prerequisites", []) or []:
            cur.execute(
                "INSERT INTO course_prerequisites (course_id, prerequisite_code) VALUES (?,?)",
                (course_id, prereq_code),
            )

    # academic_plan + academic_plan_courses
    for plan in data.get("academic_plan", []):
        cur.execute(
            "INSERT INTO academic_plans (program_id, year, semester, plan_type, total_credits) VALUES (?,?,?,?,?)",
            (program_id, plan.get("year"), plan.get("semester"), plan.get("plan_type"), plan.get("total_credits")),
        )
        plan_id = cur.lastrowid
        for pc in plan.get("courses", []):
            total, lec, lab, self_ = parse_credits(pc.get("credits"))
            matched_id = code_to_course_id.get(pc.get("code"))
            cur.execute(
                """INSERT INTO academic_plan_courses
                   (plan_id, code, name_en, credits_raw, credits_total, lecture_hours, lab_hours,
                    self_study_hours, matched_course_id)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (plan_id, pc.get("code"), pc.get("name_en"), pc.get("credits"),
                 total, lec, lab, self_, matched_id),
            )

    # career_opportunities
    for career in data.get("career_opportunities", []):
        cur.execute(
            "INSERT INTO career_opportunities (program_id, career_text) VALUES (?,?)",
            (program_id, career),
        )

    # program_outcomes (split "PLO 1 : description" into code/description when possible)
    for outcome in data.get("program_outcomes", []):
        m = re.match(r"^\s*(PLO\s*\d+)\s*[:：]\s*(.*)$", outcome)
        if m:
            plo_code, desc = m.group(1), m.group(2)
        else:
            plo_code, desc = None, outcome
        cur.execute(
            "INSERT INTO program_outcomes (program_id, plo_code, description) VALUES (?,?,?)",
            (program_id, plo_code, desc),
        )

    # special_tracks
    for track in data.get("special_tracks", []):
        cur.execute(
            "INSERT INTO special_tracks (program_id, track_text) VALUES (?,?)",
            (program_id, track),
        )

    # extraction_meta
    meta = data.get("_extract_meta", {})
    u1 = meta.get("usage_part1", {}) or {}
    u2 = meta.get("usage_part2", {}) or {}
    cur.execute(
        """INSERT INTO extraction_meta
           (program_id, model, source_files, part1_success, part2_success,
            prompt_tokens_part1, completion_tokens_part1, cost_usd_part1,
            prompt_tokens_part2, completion_tokens_part2, cost_usd_part2)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (
            program_id, "qwen", json.dumps(meta.get("source_files", []), ensure_ascii=False),
            int(bool(meta.get("part1_success"))), int(bool(meta.get("part2_success"))),
            u1.get("prompt_tokens"), u1.get("completion_tokens"), u1.get("cost_usd"),
            u2.get("prompt_tokens"), u2.get("completion_tokens"), u2.get("cost_usd"),
        ),
    )


COOP_NAME_RE = re.compile(r"COOPERATIVE|สหกิจ", re.IGNORECASE)


def find_coop_courses(cur, program_id: int):
    """หาวิชาสหกิจศึกษา (เลือก) ของหลักสูตรนี้จากตาราง courses ที่ extract มาแล้ว
    (ไม่ได้เดา — ใช้เฉพาะวิชาที่ qwen สกัดมาจริงและมีชื่อ/ชื่ออังกฤษตรงกับรูปแบบสหกิจ)"""
    rows = cur.execute(
        """SELECT course_id, code, name_th, name_en, credits_raw, credits_total,
                  lecture_hours, lab_hours, self_study_hours
           FROM courses WHERE program_id = ? AND type = 'เลือก'""",
        (program_id,),
    ).fetchall()
    return [
        r for r in rows
        if COOP_NAME_RE.search(r[3] or "") or COOP_NAME_RE.search(r[2] or "")
    ]


def is_placeholder_course(code: str, name_en: str) -> bool:
    """แถวใน academic_plan_courses ถือเป็น 'ช่องวิชาเลือก/placeholder' ถ้า code มี 'x'
    (เช่น 060164xx, 9064xxxx, xxxxxxxx) หรือชื่อมีคำว่า ELECTIVE — สัญญาณเดียวกับที่ใช้
    อธิบายรหัส placeholder ของ AIT ในหมายเหตุข้อมูลชุดนี้อยู่แล้ว"""
    code_l = (code or "").lower()
    name_l = (name_en or "").lower()
    return "x" in code_l or "elective" in name_l


def build_derived_coop_plan(cur, program_id: int, program_code: str) -> None:
    """สร้างแผน plan_type='สหกิจ' แบบ derived จากแผน 'ปกติ' ที่มีอยู่แล้ว โดยอิงข้อมูลที่
    qwen extract มาเท่านั้น (ไม่แตะ GT): หาวิชาสหกิจในตาราง courses ของหลักสูตรนี้ แล้วหา
    เทอมเดียวที่หน่วยกิตรวมของช่องวิชาเลือก/placeholder ตรงกับหน่วยกิตวิชาสหกิจพอดีแบบ
    unique (เจอเทอมเดียวเท่านั้น) ถ้าไม่ unique (ไม่เจอ หรือเจอมากกว่า 1 เทอม) จะข้ามและ
    เตือนแทนการเดา — แถวที่สร้างจะถูก mark derived=1 เพื่อแยกจากข้อมูลที่ extract มาจริง"""
    existing_types = {
        r[0] for r in cur.execute(
            "SELECT DISTINCT plan_type FROM academic_plans WHERE program_id = ?", (program_id,)
        ).fetchall()
    }
    if len(existing_types) > 1:
        print(f"  [ข้าม derived coop plan] {program_code}: มีมากกว่า 1 plan_type อยู่แล้วจากการ extract ({existing_types})")
        return

    coop_courses = find_coop_courses(cur, program_id)
    if not coop_courses:
        print(f"  [ข้าม derived coop plan] {program_code}: ไม่พบวิชาสหกิจในตาราง courses")
        return

    coop_credit_values = {r[5] for r in coop_courses if r[5] is not None}
    if len(coop_credit_values) != 1:
        print(f"  [ข้าม derived coop plan] {program_code}: หน่วยกิตวิชาสหกิจไม่ชัดเจน/ไม่ตรงกัน {coop_credit_values}")
        return
    coop_credits = coop_credit_values.pop()

    base_plans = cur.execute(
        """SELECT plan_id, year, semester, total_credits FROM academic_plans
           WHERE program_id = ? ORDER BY year, semester""",
        (program_id,),
    ).fetchall()

    plan_courses = {}
    candidates = []
    for plan_id, year, semester, total_credits in base_plans:
        rows = cur.execute(
            """SELECT id, code, name_en, credits_raw, credits_total, lecture_hours,
                      lab_hours, self_study_hours, matched_course_id
               FROM academic_plan_courses WHERE plan_id = ?""",
            (plan_id,),
        ).fetchall()
        plan_courses[plan_id] = rows
        placeholder_rows = [r for r in rows if is_placeholder_course(r[1], r[2])]
        if not placeholder_rows or not all(r[4] is not None for r in placeholder_rows):
            continue
        # ทุกช่อง placeholder ในเทอมนี้ต้องเป็น "วิชาเฉพาะสาขา" (code ขึ้นต้น '06' ตาม
        # convention ของชุดข้อมูลนี้ — 06xx=เฉพาะสาขา, 90xx=ศึกษาทั่วไป/ภาษา, xxxxxxxx=เลือกเสรี)
        # ไม่งั้นจะไปแมตช์กับเทอมที่บังเอิญหน่วยกิตรวมเท่ากันแต่เป็นวิชาศึกษาทั่วไป/เลือกเสรีแทน
        all_major_specific = all((r[1] or "").strip().lower().startswith("06") for r in placeholder_rows)
        if all_major_specific and sum(r[4] for r in placeholder_rows) == coop_credits:
            candidates.append(plan_id)

    plan_year = {pid: y for pid, y, s, tc in base_plans}
    tie_broken = False
    if len(candidates) > 1:
        # เทียบเท่ากันหลายเทอม — ใช้ convention ทั่วไปของหลักสูตรไทยว่าสหกิจศึกษามักอยู่
        # ปีสุดท้าย: เลือกเทอมที่ปีสูงสุดในบรรดาเทอมที่ตรงกัน (ยังต้อง unique หลัง tie-break ด้วย)
        max_year = max(plan_year[pid] for pid in candidates)
        narrowed = [pid for pid in candidates if plan_year[pid] == max_year]
        if len(narrowed) == 1:
            candidates = narrowed
            tie_broken = True

    if len(candidates) != 1:
        print(f"  [ข้าม derived coop plan] {program_code}: หาเทอมที่หน่วยกิต placeholder ตรงกับวิชาสหกิจ "
              f"({coop_credits} หน่วยกิต) แบบ unique ไม่เจอ (พบ {len(candidates)} เทอมที่ตรง แม้ tie-break ด้วยปีสูงสุดแล้ว)")
        return
    target_plan_id = candidates[0]

    for plan_id, year, semester, total_credits in base_plans:
        cur.execute(
            """INSERT INTO academic_plans (program_id, year, semester, plan_type, total_credits, derived)
               VALUES (?,?,?,?,?,1)""",
            (program_id, year, semester, "สหกิจ", total_credits),
        )
        new_plan_id = cur.lastrowid
        for row in plan_courses[plan_id]:
            _id, code, name_en, credits_raw, credits_total, lec, lab, self_, matched_id = row
            if plan_id == target_plan_id and is_placeholder_course(code, name_en):
                continue  # แทนที่ด้วยวิชาสหกิจด้านล่าง ไม่ copy ช่อง placeholder เดิม
            cur.execute(
                """INSERT INTO academic_plan_courses
                   (plan_id, code, name_en, credits_raw, credits_total, lecture_hours,
                    lab_hours, self_study_hours, matched_course_id)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (new_plan_id, code, name_en, credits_raw, credits_total, lec, lab, self_, matched_id),
            )
        if plan_id == target_plan_id:
            for c in coop_courses:
                course_id, code, name_th, name_en, credits_raw, credits_total, lec, lab, self_ = c
                cur.execute(
                    """INSERT INTO academic_plan_courses
                       (plan_id, code, name_en, credits_raw, credits_total, lecture_hours,
                        lab_hours, self_study_hours, matched_course_id)
                       VALUES (?,?,?,?,?,?,?,?,?)""",
                    (new_plan_id, code, name_en, credits_raw, credits_total, lec, lab, self_, course_id),
                )

    target_year, target_semester = next(
        (y, s) for pid, y, s, tc in base_plans if pid == target_plan_id
    )
    tie_note = " [ใช้ tie-break: เลือกปีสูงสุด]" if tie_broken else ""
    print(f"  [OK] derived coop plan: {program_code} ปี {target_year} เทอม {target_semester} "
          f"(swap placeholder -> วิชาสหกิจ {coop_credits} หน่วยกิต, clone {len(base_plans)} เทอมเป็น plan_type='สหกิจ'){tie_note}")


def main():
    if len(sys.argv) != 3:
        print(__doc__)
        sys.exit(1)
    src_dir = Path(sys.argv[1])
    out_path = Path(sys.argv[2])

    if out_path.exists():
        out_path.unlink()

    conn = sqlite3.connect(out_path)
    cur = conn.cursor()
    cur.executescript(SCHEMA)

    for program_code in PROGRAMS:
        json_path = src_dir / program_code / f"{program_code}_qwen.json"
        if not json_path.exists():
            print(f"[WARN] missing {json_path}, skipping")
            continue
        with open(json_path, encoding="utf-8") as f:
            data = json.load(f)
        load_program(cur, program_code, data)
        print(f"[OK] loaded {program_code}")

    print("\nBuilding derived coop plans (heuristic, from qwen extraction only)...")
    for program_code in PROGRAMS:
        row = cur.execute(
            "SELECT program_id FROM programs WHERE program_code = ?", (program_code,)
        ).fetchone()
        if row is None:
            continue
        build_derived_coop_plan(cur, row[0], program_code)

    conn.commit()

    # quick sanity report
    for table in ["programs", "courses", "academic_plans", "academic_plan_courses",
                  "career_opportunities", "program_outcomes", "special_tracks"]:
        n = cur.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        print(f"  {table}: {n} rows")

    conn.close()
    print(f"\nDone -> {out_path}")


if __name__ == "__main__":
    main()
