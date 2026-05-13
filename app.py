from flask import Flask, request, jsonify, render_template
import sqlite3
from datetime import datetime
import requests
import schedule
import time
import threading
import psycopg2
import os

app = Flask(__name__)
def get_conn():
    return psycopg2.connect(os.environ["DATABASE_URL"])
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(BASE_DIR, "data.db")

def init_db():
    conn = get_conn()
    c = conn.cursor()

    # 商品清單
    c.execute("""
        CREATE TABLE IF NOT EXISTS items (
            id SERIAL PRIMARY KEY,
            name TEXT,
            expire_date TEXT,
            last_notified TEXT
        )
    """)

    # 條碼商品快取
    c.execute("""
        CREATE TABLE IF NOT EXISTS barcode_products (
            barcode TEXT PRIMARY KEY,
            product_name TEXT
        )
    """)

    conn.commit()
    conn.close()
init_db()

# 🔹 顯示前端頁面
@app.route("/")
def home():
    return render_template("index.html")

# 🔹 取得資料
@app.route("/items", methods=["GET"])
def get_items():
    conn = get_conn()
    c = conn.cursor()
    c.execute("""
        SELECT id, name, expire_date, last_notified
        FROM items
    """)
    rows = c.fetchall()
    conn.close()

    items = []
    today = datetime.now().date()

    for r in rows:
        expire = datetime.strptime(r[2], "%Y-%m-%d").date()
        days_left = (expire - today).days

        items.append({
            "id": r[0],
            "name": r[1],
            "expire_date": r[2],
            "days_left": days_left
        })

    return jsonify(items)

# 🔹 新增
@app.route("/items", methods=["POST"])
def add_item():
    data = request.json
    print("收到資料:", data)
    conn = get_conn()
    c = conn.cursor()
    c.execute(
    "INSERT INTO items (name, expire_date, last_notified) VALUES (%s, %s, %s)",
        (data["name"], data["expire_date"], None)
    )
    conn.commit()
    conn.close()
    return jsonify({"message": "新增成功"})

# 🔹 刪除
@app.route("/items/<int:item_id>", methods=["DELETE"])
def delete_item(item_id):
    conn = get_conn()
    c = conn.cursor()
    c.execute("DELETE FROM items WHERE id=%s", (item_id,))
    conn.commit()
    conn.close()
    return jsonify({"message": "刪除成功"})

@app.route("/scanner")
def scanner():
    return render_template("scanner.html")

@app.route("/product/<barcode>")
def get_product(barcode):

    conn = get_conn()
    c = conn.cursor()

    # 🔍 查本地快取
    c.execute(
        "SELECT product_name FROM barcode_products WHERE barcode=%s",
        (barcode,)
    )

    row = c.fetchone()

    if row:
        conn.close()
        return jsonify({
            "success": True,
            "name": row[0],
            "source": "local"
        })

    try:
        url = f"https://world.openfoodfacts.org/api/v0/product/{barcode}.json"

        headers = {
            "User-Agent": "Mozilla/5.0"
        }

        res = requests.get(url, headers=headers, timeout=5)

        # ❗ API 失敗
        if res.status_code != 200:
            print("API HTTP錯誤:", res.status_code)
            return jsonify({"success": False})

        data = res.json()

        # ❗ 沒找到商品
        if data.get("status") != 1:
            return jsonify({"success": False})

        product = data.get("product", {})

        name = (
            product.get("product_name")
            or product.get("product_name_zh")
            or "未知商品"
        )

        conn.close()

        return jsonify({
            "success": True,
            "name": name,
            "source": "openfoodfacts"
        })

    except Exception as e:
        conn.close()
        print("商品查詢錯誤:", e)
        return jsonify({"success": False})
    
# 👇👇👇 直接貼這裡
@app.route("/save_barcode", methods=["POST"])
def save_barcode():

    data = request.json

    barcode = data.get("barcode")
    name = data.get("name")

    if not barcode or not name:
        return jsonify({
            "success": False,
            "message": "缺少資料"
        }), 400

    conn = get_conn()
    c = conn.cursor()

    c.execute("""
        INSERT OR REPLACE INTO barcode_products
        (barcode, product_name)
        VALUES (%s, %s)
    """, (barcode, name))

    conn.commit()
    conn.close()

    print("已手動回存:", barcode, name)

    return jsonify({
        "success": True
    })



def send_discord(msg):
    url = "https://discord.com/api/webhooks/1501067262768054414/V7ESMnm-4kLh2OO43oLBDo9m9J65CzejyEFlo1glaQL3lih_huC2QlSQd3iV1RkejXFi"
    data = {"content": msg}

    try:
        res = requests.post(url, json=data)
        if res.status_code != 204:
            print("Discord 發送失敗:", res.text)
    except Exception as e:
        print("錯誤:", e)
def check_expiry():
    conn = get_conn()
    c = conn.cursor()

    c.execute("SELECT id, name, expire_date, last_notified FROM items")
    rows = c.fetchall()

    today = datetime.now().date()
    send_discord(f"📅 今天日期：{today}\n")
    for item_id, name, date_str, last_notified in rows:
        expire = datetime.strptime(date_str, "%Y-%m-%d").date()
        days = (expire - today).days

        # 轉換 last_notified
        last_date = None
        if last_notified:
            last_date = datetime.strptime(last_notified, "%Y-%m-%d").date()

        # 🔥 條件：快過期 + 今天沒提醒
        if days < 0:
            continue  # 已過期不提醒

        elif days <= 3 and last_date != today:
            send_discord(f"⚠️ {name} 還有 {days} 天過期")

            # 👉 記錄今天已提醒
            c.execute(
                "UPDATE items SET last_notified = %s WHERE id = %s",
                (today.strftime("%Y-%m-%d"), item_id)
            )
    send_discord("_____________________________________________"
)
    conn.commit()
    conn.close()
def run_scheduler():
    schedule.every().day.at("09:00").do(check_expiry)

    while True:
        schedule.run_pending()
        time.sleep(60)

if __name__ == "__main__":
    threading.Thread(target=run_scheduler, daemon=True).start()
    app.run(host="0.0.0.0", port=10000)