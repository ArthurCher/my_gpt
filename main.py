import os
import requests
import sqlite3
from flask import Flask, request
from openai import OpenAI
from dotenv import load_dotenv
import time

load_dotenv()

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


def send_text(chat_id, text):
    requests.post(f"{TELEGRAM_API}/sendMessage", data={"chat_id": chat_id, "text": text})


def download_file(file_id):
    file_info = requests.get(f"{TELEGRAM_API}/getFile?file_id={file_id}").json()
    file_path = file_info["result"]["file_path"]
    file_url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_path}"
    response = requests.get(file_url)
    filename = file_path.split("/")[-1]
    with open(filename, "wb") as f:
        f.write(response.content)
    return filename


@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.get_json()
    message = data.get("message", {})
    chat_id = str(message.get("chat", {}).get("id"))
    thread_id = get_or_create_thread(chat_id)

    user_text = message.get("caption") or message.get("text") or "Проанализируй файл"
    file_ids = []

    if "photo" in message:
        file_id = message["photo"][-1]["file_id"]
        filename = download_file(file_id)
        with open(filename, "rb") as f:
            uploaded = client.files.create(file=f, purpose="assistants")
        file_ids.append(uploaded.id)

    elif "document" in message:
        file_id = message["document"]["file_id"]
        filename = download_file(file_id)
        with open(filename, "rb") as f:
            uploaded = client.files.create(file=f, purpose="assistants")
        file_ids.append(uploaded.id)

    elif "text" in message:
        user_text = message["text"]

    else:
        send_text(chat_id, "Пожалуйста, отправьте текст, фото или документ.")
        return "ok"

    client.beta.threads.messages.create(
        thread_id=thread_id,
        role="user",
        content=user_text,
        file_ids=file_ids if file_ids else None
    )

    run = client.beta.threads.runs.create(
        thread_id=thread_id,
        assistant_id=os.getenv("ASSISTANT_ID"),
        tools=[{"type": "code_interpreter"}, {"type": "file_search"}]
    )

    for _ in range(30):
        run = client.beta.threads.runs.retrieve(thread_id=thread_id, run_id=run.id)
        if run.status == "completed":
            break
        elif run.status in ["failed", "cancelled", "expired"]:
            send_text(chat_id, "Произошла ошибка при обработке запроса.")
            return "ok"
        time.sleep(1)

    messages = client.beta.threads.messages.list(thread_id=thread_id)
    reply = messages.data[0].content[0].text.value
    send_text(chat_id, reply)
    return "ok"


if __name__ == "__main__":
    init_db()
    app.run(host="0.0.0.0", port=8080)
