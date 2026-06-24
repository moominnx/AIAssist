"""
evaluate_alternative.py
========================

วิธี eval ที่ไม่พึ่ง teach-table GT โดยตรง:

Method 1 — Cross-model agreement
  วัดว่า 3 โมเดลตกลงกันมากแค่ไหนในแต่ละโปรแกรม
  3/3 agree = high confidence | 2/3 = review | 1/3 = suspicious

Method 2 — Schema & sanity validation
  ตรวจ format ของข้อมูลที่ extract มาโดยไม่ต้องใช้ GT
  - รหัสวิชา: 8 หลัก ตัวเลขล้วน
  - credit format: N(L-P-S) หรือ N
  - หน่วยกิตรวมต่อ category ตรงกับ curriculum_structure มั้ย
  - prerequisite codes อยู่ใน course list เดียวกันมั้ย
  - year/semester อยู่ใน range ที่สมเหตุสมผลมั้ย

Output:
  agreement_summary.csv   — สรุป agreement ต่อโปรแกรม
  agreement_details.csv   — รายวิชา พร้อม agreement level
  schema_report.csv       — รายการ violation ต่อโมเดล+โปรแกรม
"""

import csv, json, re
from collections import defaultdict
from pathlib import Path

BASE_DIR    = Path(__file__).parent
EXTRACT_DIR = BASE_DIR / "extracted_teach_table"
PROGRAMS    = ["AIT", "BIT", "DSBA", "IT"]
MODELS      = ["deepseek", "gemini", "qwen"]

CREDIT_TOTAL = {"AIT": 120, "BIT": 126, "DSBA": 132, "IT": 129}

# ────────────────────────────────────────────────────────────────
# Helpers
# ────────────────────────────────────────────────────────────────

def load(program: str, model: str) -> dict:
    path = EXTRACT_DIR / program / f"{program}_{model}.json"
    return json.loads(path.read_text()) if path.exists() else {}

def is_placeholder(code: str) -> bool:
    return bool(re.search(r"x", str(code), re.I)) or not str(code).strip()

def parse_credit_value(credit_str: str) -> float | None:
    """ดึงค่าหน่วยกิตตัวเลขออกมา เช่น '3(3-0-6)' -> 3.0"""
    if not credit_str:
        return None
    m = re.match(r"^(\d+(?:\.\d+)?)", str(credit_str).strip())
    return float(m.group(1)) if m else None

def valid_code(code: str) -> bool:
    return bool(re.fullmatch(r"\d{8}", str(code).strip()))

def valid_credit_fmt(credit_str: str) -> bool:
    if not credit_str:
        return False
    # ยอมรับ: 3 | 3(3-0-6) | 6(0-35-0) | 0(0-0-45)
    return bool(re.match(r"^\d+(\(\d+-\d+-\d+\))?", str(credit_str).strip()))

# ────────────────────────────────────────────────────────────────
# Method 1: Cross-model agreement
# ────────────────────────────────────────────────────────────────

def cross_model_agreement(program: str) -> tuple[list, dict]:
    """
    คืน (details_rows, summary_counts)
    agreement_level: ALL3 | TWO3 | ONE3
    """
    # โหลดทุกโมเดล กรอง placeholder
    model_codes: dict[str, set] = {}
    model_name_map: dict[str, dict] = {}   # model -> {code: name_th}
    for m in MODELS:
        data = load(program, m)
        courses = data.get("courses", [])
        codes = {c["code"] for c in courses if not is_placeholder(c.get("code",""))}
        model_codes[m] = codes
        model_name_map[m] = {c["code"]: (c.get("name_th",""), c.get("name_en",""))
                             for c in courses if not is_placeholder(c.get("code",""))}

    all_codes = set().union(*model_codes.values())
    counts = defaultdict(int)
    details = []

    for code in sorted(all_codes):
        models_with = [m for m in MODELS if code in model_codes[m]]
        n = len(models_with)
        level = "ALL3" if n == 3 else ("TWO3" if n == 2 else "ONE3")
        counts[level] += 1

        # รวบรวมชื่อจากโมเดลที่มี
        names_th = list({model_name_map[m][code][0] for m in models_with if model_name_map[m].get(code)})
        name_agree = len(names_th) == 1   # ชื่อตรงกันทุกโมเดล

        details.append({
            "program":       program,
            "code":          code,
            "agreement":     level,
            "n_models":      n,
            "models_agreed": "|".join(models_with),
            "name_th":       names_th[0] if names_th else "",
            "name_match":    "yes" if name_agree else "no",
            "name_variants": " / ".join(names_th) if not name_agree else "",
        })

    total = len(all_codes)
    summary = {
        "program":        program,
        "total_unique":   total,
        "ALL3_n":         counts["ALL3"],
        "ALL3_pct":       round(100*counts["ALL3"]/total, 1) if total else 0,
        "TWO3_n":         counts["TWO3"],
        "TWO3_pct":       round(100*counts["TWO3"]/total, 1) if total else 0,
        "ONE3_n":         counts["ONE3"],
        "ONE3_pct":       round(100*counts["ONE3"]/total, 1) if total else 0,
    }
    return details, summary


# ────────────────────────────────────────────────────────────────
# Method 2: Schema & sanity validation
# ────────────────────────────────────────────────────────────────

def build_credit_lookup(data: dict) -> dict:
    """
    สร้าง {code: credits} จาก academic_plan ทุก semester
    ใช้ backfill courses[] ที่ไม่มี credits (เช่น DSBA/deepseek)
    normalize "3 (3-0-6)" → "3(3-0-6)"
    """
    lookup = {}
    for entry in data.get("academic_plan", []):
        for c in entry.get("courses", []):
            if isinstance(c, str):
                continue   # gemini เก็บแค่ code string ไม่มี credits
            code = c.get("code", "")
            cr   = c.get("credits", "")
            if code and cr and code not in lookup:
                # normalize space ก่อนวงเล็บ: "3 (3-0-6)" → "3(3-0-6)"
                lookup[code] = re.sub(r"\s+\(", "(", str(cr).strip())
    return lookup


def schema_validate(program: str, model: str) -> list[dict]:
    data = load(program, model)
    if not data:
        return []

    courses   = data.get("courses", [])
    curr      = data.get("curriculum_structure", {})
    prog_info = data.get("program_info", {})
    violations = []

    # backfill credits จาก academic_plan ถ้า courses[] ไม่มี
    credit_lookup = build_credit_lookup(data)
    backfilled = 0
    for c in courses:
        if not c.get("credits") and c.get("code") in credit_lookup:
            c["credits"] = credit_lookup[c["code"]]
            backfilled += 1
    if backfilled:
        violations.append({
            "program": program, "model": model,
            "code": "—", "name_th": "—",
            "check": "INFO_CREDITS_BACKFILLED",
            "detail": f"backfill credits จาก academic_plan {backfilled} วิชา",
        })

    def flag(code, name, check, detail):
        violations.append({
            "program": program, "model": model,
            "code": code, "name_th": name,
            "check": check, "detail": detail,
        })

    real_courses = [c for c in courses if not is_placeholder(c.get("code",""))]
    code_set = {c["code"] for c in real_courses}

    for c in real_courses:
        code    = c.get("code", "")
        name_th = c.get("name_th", "")
        credit  = c.get("credits", "")
        year    = c.get("year")
        sem     = c.get("semester")
        prereqs = c.get("prerequisites", [])

        # 1. รหัสวิชา format
        if not valid_code(code):
            flag(code, name_th, "INVALID_CODE_FORMAT",
                 f"'{code}' ไม่ใช่ 8 หลักตัวเลข")

        # 2. credit format
        if not valid_credit_fmt(credit):
            flag(code, name_th, "INVALID_CREDIT_FORMAT",
                 f"'{credit}' ไม่ตรง pattern N(L-P-S)")

        # 3. year/semester range
        if year is not None and year not in (0, 1, 2, 3, 4):
            flag(code, name_th, "INVALID_YEAR", f"year={year}")
        if sem is not None and sem not in (0, 1, 2, 3):
            flag(code, name_th, "INVALID_SEMESTER", f"semester={sem}")

        # 4. prerequisite codes ต้องอยู่ใน course list เดียวกัน
        for prereq in prereqs:
            p_code = prereq.get("code","") if isinstance(prereq, dict) else str(prereq)
            if p_code and not is_placeholder(p_code) and p_code not in code_set:
                flag(code, name_th, "PREREQ_NOT_IN_LIST",
                     f"prereq {p_code} ไม่อยู่ใน extracted courses")

    # 5. Credit sum vs curriculum_structure
    def sum_credits_by_cat(cat_keyword: str) -> float:
        return sum(
            parse_credit_value(c.get("credits","")) or 0
            for c in real_courses
            if cat_keyword in (c.get("category","") or "")
        )

    total_declared = parse_credit_value(str(prog_info.get("total_credits","")))
    total_extracted = sum(parse_credit_value(c.get("credits","")) or 0
                          for c in real_courses)

    expected_total = CREDIT_TOTAL.get(program)
    if expected_total and total_declared and abs(total_declared - expected_total) > 0:
        flag("—", "program_info", "TOTAL_CREDITS_WRONG",
             f"declared={total_declared} แต่หลักสูตร {program}={expected_total}")

    ge_declared  = curr.get("general_education",{}).get("credits")
    sp_declared  = curr.get("specific_courses",{}).get("credits")
    ge_extracted = sum_credits_by_cat("ศึกษาทั่วไป")
    sp_extracted = sum_credits_by_cat("เฉพาะ")

    TOLERANCE = 3   # ยืดหยุ่น ±3 หน่วยกิต เพราะวิชาเลือกนับซ้อนได้

    if ge_declared and abs(ge_extracted - ge_declared) > TOLERANCE:
        flag("—", "credit_sum", "GE_CREDIT_SUM_MISMATCH",
             f"extracted={ge_extracted:.0f} declared={ge_declared}")

    if sp_declared and abs(sp_extracted - sp_declared) > TOLERANCE:
        flag("—", "credit_sum", "SPECIFIC_CREDIT_SUM_MISMATCH",
             f"extracted={sp_extracted:.0f} declared={sp_declared}")

    return violations


# ────────────────────────────────────────────────────────────────
# Main
# ────────────────────────────────────────────────────────────────

def main():
    all_agree_details = []
    all_agree_summary = []
    all_schema        = []

    for prog in PROGRAMS:
        print(f"\n{'='*50}")
        print(f"โปรแกรม: {prog}")

        # Method 1
        details, summary = cross_model_agreement(prog)
        all_agree_details.extend(details)
        all_agree_summary.append(summary)
        print(f"  Agreement — ALL3={summary['ALL3_pct']}%  "
              f"TWO3={summary['TWO3_pct']}%  ONE3={summary['ONE3_pct']}%  "
              f"(จาก {summary['total_unique']} unique codes)")

        # Method 2
        for model in MODELS:
            viols = schema_validate(prog, model)
            all_schema.extend(viols)
            by_check = defaultdict(int)
            for v in viols:
                by_check[v["check"]] += 1
            viol_str = ", ".join(f"{k}={n}" for k,n in sorted(by_check.items())) or "ไม่มี"
            print(f"  Schema [{model:10s}] violations: {viol_str}")

    # ─── Save ────────────────────────────────────────────────────

    agree_sum_path = BASE_DIR / "agreement_summary.csv"
    with open(agree_sum_path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(all_agree_summary[0].keys()))
        w.writeheader(); w.writerows(all_agree_summary)

    agree_det_path = BASE_DIR / "agreement_details.csv"
    with open(agree_det_path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(all_agree_details[0].keys()))
        w.writeheader(); w.writerows(all_agree_details)

    schema_path = BASE_DIR / "schema_report.csv"
    if all_schema:
        with open(schema_path, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=list(all_schema[0].keys()))
            w.writeheader(); w.writerows(all_schema)
    else:
        schema_path.write_text("no violations")

    print(f"\n[+] agreement_summary.csv → {agree_sum_path}")
    print(f"[+] agreement_details.csv → {agree_det_path}")
    print(f"[+] schema_report.csv     → {schema_path} ({len(all_schema)} violations)")

    # ─── Quick summary ───────────────────────────────────────────
    one3 = [r for r in all_agree_details if r["agreement"] == "ONE3"]
    print(f"\n[i] ONE3 (น่าตรวจ) ทั้งหมด {len(one3)} รายการ:")
    for r in one3[:8]:
        print(f"  [{r['program']}] {r['code']} — {r['name_th'][:40]} (เฉพาะ {r['models_agreed']})")

    name_mismatch = [r for r in all_agree_details
                     if r["agreement"] == "ALL3" and r["name_match"] == "no"]
    print(f"\n[i] ALL3 แต่ชื่อต่างกัน ({len(name_mismatch)} รายการ):")
    for r in name_mismatch[:5]:
        print(f"  [{r['program']}] {r['code']} — {r['name_variants'][:70]}")


if __name__ == "__main__":
    main()
