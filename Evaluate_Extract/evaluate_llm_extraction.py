"""
evaluate_llm_extraction.py
============================

เปรียบเทียบผล extract ของ 3 LLM (deepseek, gemini, qwen) ต่อ 4 โปรแกรม
(AIT, BIT, DSBA, IT) กับ ground truth จาก Registrar API

Input:
  extracted_teach_table/<PROGRAM>/<PROGRAM>_<model>.json  — ผล LLM (courses[].code/name_th/name_en)
  GT/<PROGRAM>/subject_index.json                          — ground truth (subject_id/name_th/name_en)
  deprecated_codes.json                                    — (optional) รหัสวิชาหลักสูตรเก่า

Output:
  eval_summary.csv   — สรุปต่อ model × program
  eval_details.csv   — ทุกวิชาที่ตรวจ พร้อม status
"""

import csv
import difflib
import json
import re
from collections import defaultdict
from pathlib import Path

# ─── CONFIG ─────────────────────────────────────────────────────────────────

BASE_DIR           = Path(__file__).parent
EXTRACT_DIR        = BASE_DIR / "extracted_teach_table"
GT_DIR             = BASE_DIR / "GT"
DEPRECATED_FILE    = BASE_DIR / "deprecated_codes.json"

PROGRAMS = ["AIT", "BIT", "DSBA", "IT"]
MODELS   = ["deepseek", "gemini", "qwen"]

NAME_SIMILARITY_THRESHOLD = 0.82   # fuzzy threshold สำหรับ name match

# ─── HELPERS ────────────────────────────────────────────────────────────────

def normalize(text: str) -> str:
    if not text:
        return ""
    text = str(text).strip().upper()
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"[^\w฀-๿ ]", "", text)
    return text


def name_sim(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, normalize(a), normalize(b)).ratio()


def is_placeholder(code: str) -> bool:
    """ข้ามรหัสวิชาที่เป็น placeholder เช่น 9064xxxx, xxxxxxxx"""
    return bool(re.search(r"x", str(code), re.IGNORECASE)) or not code.strip()


# ─── LOADERS ────────────────────────────────────────────────────────────────

def load_gt_index(program: str) -> dict:
    """
    โหลด GT/<program>/subject_index.json
    คืน dict: {subject_id_normalized: {name_th, name_en, credit_str}}
    """
    path = GT_DIR / program / "subject_index.json"
    if not path.exists():
        raise FileNotFoundError(f"ไม่พบ ground truth: {path}")
    subjects = json.loads(path.read_text())
    index = {}
    for s in subjects:
        sid = normalize(s.get("subject_id", ""))
        if not sid:
            continue
        index[sid] = {
            "name_th":   s.get("name_th", ""),
            "name_en":   s.get("name_en", ""),
            "credit_str": s.get("credit_str", ""),
        }
    return index


def load_llm_courses(program: str, model: str) -> list[dict]:
    """
    โหลด extracted_teach_table/<program>/<program>_<model>.json
    คืน list ของ course dict (code, name_th, name_en, credits, ...)
    กรอง placeholder codes ออก
    """
    path = EXTRACT_DIR / program / f"{program}_{model}.json"
    if not path.exists():
        return []
    data = json.loads(path.read_text())
    courses = data.get("courses", [])
    return [c for c in courses if not is_placeholder(c.get("code", ""))]


def load_deprecated() -> set:
    if not DEPRECATED_FILE.exists():
        return set()
    codes = json.loads(DEPRECATED_FILE.read_text())
    return {normalize(c) for c in codes}


# ─── CLASSIFICATION ──────────────────────────────────────────────────────────

def classify(code: str, name_th: str, name_en: str,
             gt_index: dict, deprecated: set) -> tuple[str, str]:
    """
    คืน (status, matched_gt_id)

    EXACT_MATCH              code ตรง และชื่อ (th หรือ en) ตรง
    CODE_MATCH_NAME_MISMATCH code ตรง แต่ชื่อไม่ตรง
    NAME_MATCH_CODE_MISMATCH code ไม่ตรง แต่ชื่อคล้ายวิชาอื่นใน GT
    NOT_FOUND_DEPRECATED     code ไม่ตรง + อยู่ใน deprecated whitelist
    NOT_FOUND_UNKNOWN        code ไม่ตรง + ไม่ใช่ deprecated (น่าสงสัย)
    """
    code_norm = normalize(code)

    if code_norm in gt_index:
        gt = gt_index[code_norm]
        sim = max(name_sim(name_th, gt["name_th"]),
                  name_sim(name_en, gt["name_en"]))
        if sim >= NAME_SIMILARITY_THRESHOLD:
            return "EXACT_MATCH", code_norm
        return "CODE_MATCH_NAME_MISMATCH", code_norm

    # code ไม่เจอ — ลองหา name match ทั่ว GT
    best_sid, best_sim = "", 0.0
    for sid, gt in gt_index.items():
        sim = max(name_sim(name_th, gt["name_th"]),
                  name_sim(name_en, gt["name_en"]))
        if sim > best_sim:
            best_sim, best_sid = sim, sid

    if best_sim >= NAME_SIMILARITY_THRESHOLD:
        return "NAME_MATCH_CODE_MISMATCH", best_sid

    if code_norm in deprecated:
        return "NOT_FOUND_DEPRECATED", ""

    return "NOT_FOUND_UNKNOWN", ""


# ─── MAIN ────────────────────────────────────────────────────────────────────

def main():
    deprecated = load_deprecated()
    print(f"[i] deprecated codes: {len(deprecated)} รายการ")

    all_details  = []
    summary_rows = []

    for program in PROGRAMS:
        print(f"\n{'='*50}")
        print(f"โปรแกรม: {program}")

        try:
            gt_index = load_gt_index(program)
        except FileNotFoundError as e:
            print(f"[!] {e} — ข้ามโปรแกรมนี้")
            continue
        print(f"[+] GT index: {len(gt_index)} วิชา")

        for model in MODELS:
            courses = load_llm_courses(program, model)
            if not courses:
                print(f"[!] ไม่พบ/ไม่มีข้อมูลของ {model} — ข้าม")
                continue

            counts: dict[str, int] = defaultdict(int)
            for c in courses:
                code     = c.get("code", "")
                name_th  = c.get("name_th", "")
                name_en  = c.get("name_en", "")
                credits  = c.get("credits", "")

                status, matched = classify(code, name_th, name_en, gt_index, deprecated)
                counts[status] += 1

                all_details.append({
                    "program":             program,
                    "model":               model,
                    "extracted_code":      code,
                    "extracted_name_th":   name_th,
                    "extracted_name_en":   name_en,
                    "extracted_credits":   credits,
                    "status":              status,
                    "matched_gt_id":       matched,
                })

            total = sum(counts.values())
            def pct(k): return round(100 * counts[k] / total, 1) if total else 0.0

            row = {
                "program":                    program,
                "model":                      model,
                "total_extracted":            total,
                "exact_match_pct":            pct("EXACT_MATCH"),
                "code_match_name_mismatch_pct": pct("CODE_MATCH_NAME_MISMATCH"),
                "name_match_code_mismatch_pct": pct("NAME_MATCH_CODE_MISMATCH"),
                "not_found_deprecated_pct":   pct("NOT_FOUND_DEPRECATED"),
                "not_found_unknown_pct":      pct("NOT_FOUND_UNKNOWN"),
                "exact_match_n":              counts["EXACT_MATCH"],
                "not_found_unknown_n":        counts["NOT_FOUND_UNKNOWN"],
            }
            summary_rows.append(row)

            print(f"  [{model:10s}] total={total:3d} | "
                  f"EXACT={row['exact_match_pct']:5.1f}% | "
                  f"CODE_OK_NAME_FAIL={row['code_match_name_mismatch_pct']:4.1f}% | "
                  f"NAME_OK_CODE_FAIL={row['name_match_code_mismatch_pct']:4.1f}% | "
                  f"UNKNOWN={row['not_found_unknown_pct']:4.1f}%")

    # ─── บันทึก summary ──────────────────────────────────────────────────────
    summary_path = BASE_DIR / "eval_summary.csv"
    if summary_rows:
        with open(summary_path, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=list(summary_rows[0].keys()))
            writer.writeheader()
            writer.writerows(summary_rows)
        print(f"\n[+] eval_summary.csv → {summary_path}")

    # ─── บันทึก details ──────────────────────────────────────────────────────
    details_path = BASE_DIR / "eval_details.csv"
    if all_details:
        with open(details_path, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=list(all_details[0].keys()))
            writer.writeheader()
            writer.writerows(all_details)
        print(f"[+] eval_details.csv  → {details_path}")

    # ─── สรุปภาพรวม ──────────────────────────────────────────────────────────
    if not summary_rows:
        return

    print("\n" + "="*60)
    print("สรุปภาพรวมต่อโมเดล (เฉลี่ยทุกโปรแกรม)")
    print("="*60)

    from collections import defaultdict as dd
    model_totals: dict = dd(lambda: defaultdict(float))
    model_counts: dict = dd(int)

    for r in summary_rows:
        m = r["model"]
        model_totals[m]["exact"]   += r["exact_match_pct"]
        model_totals[m]["unknown"] += r["not_found_unknown_pct"]
        model_counts[m] += 1

    for m in MODELS:
        n = model_counts[m]
        if n == 0:
            continue
        avg_exact   = model_totals[m]["exact"]   / n
        avg_unknown = model_totals[m]["unknown"] / n
        print(f"  {m:12s}: avg exact={avg_exact:5.1f}%  avg unknown={avg_unknown:5.1f}%")

    # ─── NOT_FOUND_UNKNOWN ตัวอย่าง ──────────────────────────────────────────
    unknowns = [d for d in all_details if d["status"] == "NOT_FOUND_UNKNOWN"]
    if unknowns:
        print(f"\n[i] ตัวอย่าง NOT_FOUND_UNKNOWN ({len(unknowns)} รายการทั้งหมด):")
        seen = set()
        shown = 0
        for u in unknowns:
            key = (u["program"], u["model"], u["extracted_code"])
            if key in seen or shown >= 5:
                continue
            seen.add(key)
            shown += 1
            print(f"  [{u['program']}/{u['model']}] code={u['extracted_code']!r:15s} name_th={u['extracted_name_th'][:40]!r}")


if __name__ == "__main__":
    main()
