import os
import sqlite3
import requests
import base64
from flask import Flask, request
from werkzeug.utils import secure_filename
from openai import OpenAI

app = Flask(__name__)

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
client = OpenAI()

DB_PATH = "chat_history.db"
TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}"

os.makedirs("files", exist_ok=True)

def save_message(chat_id, role, content):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO chat_history (chat_id, role, content) VALUES (?, ?, ?)",
        (chat_id, role, content)
    )
    conn.commit()
    conn.close()

def get_chat_history(chat_id, limit=10):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        "SELECT role, content FROM chat_history WHERE chat_id = ? ORDER BY id DESC LIMIT ?",
        (chat_id, limit)
    )
    rows = cursor.fetchall()
    conn.close()
    return [{"role": role, "content": content} for role, content in reversed(rows)]

def ask_gpt(messages):
    response = client.chat.completions.create(
        model="gpt-4o",
        messages=messages
    )
    return response.choices[0].message.content.strip()

def ask_gpt_vision(image_b64):
    response = client.chat.completions.create(
        model="gpt-4-vision-preview",
        messages=[
            {"role": "user", "content": [
                {"type": "text", "text": "Опиши и проанализируй изображение"},
                {"type": "image_url", "image_url": {
                    "url": f"data:image/jpeg;base64,{image_b64}"
                }}
            ]}
        ],
        max_tokens=1000
    )
    return response.choices[0].message.content.strip()

@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.get_json()
    message = data.get("message", {})
    chat_id = str(message.get("chat", {}).get("id"))

    if "text" in message:
        user_text = message["text"]
        save_message(chat_id, "user", user_text)
        history = get_chat_history(chat_id)
        reply = ask_gpt(history)
        save_message(chat_id, "assistant", reply)
        send_text(chat_id, reply)
        return "ok"

    if "photo" in message:
        file_id = message["photo"][-1]["file_id"]
        image_b64 = download_and_encode_image(file_id)
        if image_b64:
            reply = ask_gpt_vision(image_b64)
            send_text(chat_id, reply)
        return "ok"

    if "document" in message:
        file_id = message["document"]["file_id"]
        file_name = message["document"]["file_name"]
        file_path = download_file(file_id, file_name)
        if file_path:
            file_text = extract_text_from_file(file_path)
            prompt = f"Проанализируй содержимое файла:\n\n{file_text[:3000]}"
            reply = ask_gpt([{"role": "user", "content": prompt}])
            send_text(chat_id, reply)
        return "ok"

    return "ok"

def send_text(chat_id, text):
    requests.post(f"{TELEGRAM_API}/sendMessage", data={
        "chat_id": chat_id,
        "text": text
    })

def download_file(file_id, filename):
    file_info = requests.get(f"{TELEGRAM_API}/getFile?file_id={file_id}").json()
    file_path = file_info["result"]["file_path"]
    file_url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_path}"
    local_path = os.path.join("files", secure_filename(filename))
    r = requests.get(file_url)
    with open(local_path, "wb") as f:
        f.write(r.content)
    return local_path

def download_and_encode_image(file_id):
    file_info = requests.get(f"{TELEGRAM_API}/getFile?file_id={file_id}").json()
    file_path = file_info["result"]["file_path"]
    file_url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_path}"
    r = requests.get(file_url)
    if r.status_code == 200:
        return base64.b64encode(r.content).decode("utf-8")
    return None

def extract_text_from_file(path):
    ext = os.path.splitext(path)[1].lower()
    try:
        if ext == ".txt":
            with open(path, "r", encoding="utf-8") as f:
                return f.read()
        elif ext == ".csv":
            import pandas as pd
            df = pd.read_csv(path)
            return df.to_string()
        elif ext == ".docx":
            from docx import Document
            doc = Document(path)
            return "\n".join([p.text for p in doc.paragraphs])
        elif ext == ".pdf":
            import fitz
            text = ""
            with fitz.open(path) as pdf:
                for page in pdf:
                    text += page.get_text()
            return text
    except Exception as e:
        return f"[Ошибка чтения файла: {e}]"
    return "[Неподдерживаемый тип файла]"

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
