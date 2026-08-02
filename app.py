#!/usr/bin/env python3
"""
app.py
======
หน้าเว็บทดสอบ Text-to-SQL chatbot (ห่อ text_to_sql_chatbot.py ด้วย Flask)

พิมพ์คำถามภาษาไทย -> เห็น คำตอบ + SQL ที่โมเดลเขียน + ตารางผลลัพธ์ดิบ ในหน้าเดียว

รัน:
  ตั้งค่า .env (ดู .env.example) แล้ว:
    python app.py
  เปิด browser ที่ http://localhost:5000
"""

import os

from flask import Flask, jsonify, render_template, request

from text_to_sql_chatbot import (
    build_column_value_hints,
    build_schema_description,
    build_system_prompt,
    generate_sql,
    load_dotenv,
    phrase_answer,
    run_query,
    sanitize_sql,
)

ENV_FILE = os.environ.get("ENV_FILE", ".env")
load_dotenv(ENV_FILE)

DB_PATH = os.environ.get("CHATBOT_DB_PATH", "chatbot_teach_table.db")

app = Flask(__name__)

_system_prompt = None


def get_system_prompt() -> str:
    global _system_prompt
    if _system_prompt is None:
        schema_desc = build_schema_description(DB_PATH)
        value_hints = build_column_value_hints(DB_PATH)
        _system_prompt = build_system_prompt(schema_desc, value_hints)
    return _system_prompt


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/ask", methods=["POST"])
def ask():
    try:
        data = request.get_json(force=True, silent=True) or {}
        question = (data.get("question") or "").strip()
        if not question:
            return jsonify({"error": "กรุณาพิมพ์คำถาม"}), 400

        system_prompt = get_system_prompt()

        try:
            raw_sql = generate_sql(question, system_prompt)
        except Exception as e:
            return jsonify({"error": f"เรียก LLM เขียน SQL ไม่สำเร็จ: {e}"}), 502

        sql, err = sanitize_sql(raw_sql)
        if err:
            return jsonify({"error": err, "sql": raw_sql}), 200

        cols, rows, qerr = run_query(DB_PATH, sql)
        if qerr:
            return jsonify({"error": qerr, "sql": sql}), 200

        try:
            answer = phrase_answer(question, sql, cols, rows)
        except Exception as e:
            answer = f"(เรียบเรียงคำตอบไม่สำเร็จ: {e})"

        return jsonify({
            "sql": sql,
            "columns": cols,
            "rows": rows,
            "answer": answer,
        })
    except Exception as e:
        return jsonify({"error": f"เกิดข้อผิดพลาดที่ไม่คาดคิด: {e}"}), 500


if __name__ == "__main__":
    if not os.path.exists(DB_PATH):
        raise SystemExit(f"ไม่พบไฟล์ DB: {DB_PATH}")
    app.run(debug=True, port=5000)
