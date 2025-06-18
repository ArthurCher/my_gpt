import os
import requests
import sqlite3
from flask import Flask, request
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv(dotenv_path=".env")

print("🔐 OPENAI_API_KEY:", os.getenv("OPENAI_API_KEY"))

client = OpenAI(
    api_key=os.getenv("OPENAI_API_KEY"),
    organization=os.getenv("OPENAI_ORG_ID"),
    project=os.getenv("OPENAI_PROJECT_ID")
)

app = Flask(__name__)
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}"
DB_PATH = "thread_map.db"

def init_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("CREATE TABLE IF NOT EXISTS threads (chat_id TEXT PRIMARY KEY, thread_id TEXT)")
    conn.commit()
    conn.close()

def get_or_create_thread(chat_id):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT thread_id FROM threads WHERE chat_id = ?", (chat_id,))
    row = cursor.fetchone()
    if row:
        thread_id = row[0]
    else:
        thread = client.beta.threads.create()
        thread_id = thread.id
        cursor.execute("INSERT INTO threads (chat_id, thread_id) VALUES (?, ?)", (chat_id, thread_id))
        conn.commit()
    conn.close()
    return thread_id

@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.get_json()
    message = data.get("message", {})
    chat_id = str(message.get("chat", {}).get("id"))

    if "text" not in message:
        return "ok"

    user_text = message["text"]
    thread_id = get_or_create_thread(chat_id)

    client.beta.threads.messages.create(thread_id=thread_id, role="user", content=user_text)
    run = client.beta.threads.runs.create(thread_id=thread_id, assistant_id=os.getenv("ASSISTANT_ID"))

    while True:
        run = client.beta.threads.runs.retrieve(thread_id=thread_id, run_id=run.id)
        if run.status == "completed":
            break
        elif run.status in ["failed", "cancelled", "expired"]:
            return "ok"

    messages = client.beta.threads.messages.list(thread_id=thread_id)
    reply = messages.data[0].content[0].text.value
    send_text(chat_id, reply)
    return "ok"

def send_text(chat_id, text):
    requests.post(f"{TELEGRAM_API}/sendMessage", data={
        "chat_id": chat_id,
        "text": text
    })

if __name__ == "__main__":
    init_db()
    app.run(host="0.0.0.0", port=8080)
