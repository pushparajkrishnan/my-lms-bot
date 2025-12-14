import os
import json
import random
import re
from datetime import datetime, date

import pandas as pd
from telegram import Poll
from telegram.ext import ApplicationBuilder, CommandHandler
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
import pytz

from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

# =====================
# CONFIG
# =====================
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
YOUR_CHAT_ID = int(os.environ.get("YOUR_CHAT_ID"))
GOOGLE_SHEET_ID = os.environ.get("GOOGLE_SHEET_ID")
GOOGLE_DOC_ID = os.environ.get("GOOGLE_DOC_ID")

QUIZ_TIME = "20:00"
CONCEPT_TIME = "20:05"
QUIZ_COUNT = 5

STATE_FILE = "state.json"

IST = pytz.timezone("Asia/Kolkata")

# =====================
# CREDENTIALS FROM ENV
# =====================
GOOGLE_CREDENTIALS_JSON = os.environ.get("GOOGLE_CREDENTIALS_JSON")
creds_info = json.loads(GOOGLE_CREDENTIALS_JSON)
creds = Credentials.from_service_account_info(creds_info)


# =====================
# Load or Init State
# =====================
def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r") as f:
            return json.load(f)

    state = {"start_date": date.today().isoformat()}
    save_state(state)
    return state


def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


# =====================
# GOOGLE SHEETS (QUIZ)
# =====================
def load_quiz_data():
    service = build("sheets", "v4", credentials=creds)

    result = service.spreadsheets().values().get(
        spreadsheetId=GOOGLE_SHEET_ID,
        range="Sheet1!A:B"
    ).execute()

    values = result.get("values", [])

    if not values or len(values) < 2:
        raise Exception("Google Sheet has no data.")

    df = pd.DataFrame(values[1:], columns=values[0])
    df = df.rename(columns={'Word': 'Word', 'Meaning': 'Meaning'})

    # Clean data
    df = df.dropna(subset=["Word", "Meaning"])
    df["Word"] = df["Word"].astype(str).strip()
    df["Meaning"] = df["Meaning"].astype(str).strip()
    df = df[df["Word"] != ""]
    df = df[df["Meaning"] != ""]
    df = df.drop_duplicates(subset=["Meaning"])

    return df


# =====================
# GOOGLE DOCS (CONCEPTS)
# =====================
def fetch_doc_text():
    creds_docs = Credentials.from_service_account_info(
        creds_info,
        scopes=["https://www.googleapis.com/auth/documents.readonly"]
    )

    service = build("docs", "v1", credentials=creds_docs)
    doc = service.documents().get(documentId=GOOGLE_DOC_ID).execute()

    blocks = []
    for item in doc.get("body", {}).get("content", []):
        para = item.get("paragraph")
        if para:
            for el in para.get("elements", []):
                text = el.get("textRun", {}).get("content", "")
                blocks.append(text)

    full_text = "".join(blocks)
    return full_text


SEP_PATTERN = re.compile(r"^\s*-{3,}\s*$", re.MULTILINE)


def split_concepts(full_text):
    parts = re.split(SEP_PATTERN, full_text)
    return [p.strip() for p in parts if p.strip()]


# =====================
# CONCEPT SCHEDULING
# =====================
def get_today_concept_index(n, start_date_iso):
    sd = date.fromisoformat(start_date_iso)
    today = date.today()
    days = (today - sd).days
    week = days // 7
    day_in_week = days % 7
    base = (week // 2) * 7
    return (base + day_in_week) % n


# =====================
# TELEGRAM HANDLERS
# =====================
async def send_quiz(app):
    bot = app.bot
    df = load_quiz_data()

    await bot.send_message(YOUR_CHAT_ID, "📝 Today’s Quiz (5 Questions)")

    sample = df.sample(min(QUIZ_COUNT, len(df)))

    for _, row in sample.iterrows():
        word = row["Word"].strip()
        correct = row["Meaning"].strip()

        wrong_pool = df[df["Meaning"] != correct]["Meaning"].tolist()
        wrong_pool = list(set(wrong_pool))

        if len(wrong_pool) < 3:
            continue

        wrong_choices = random.sample(wrong_pool, 3)
        options = wrong_choices + [correct]
        random.shuffle(options)

        await bot.send_poll(
            chat_id=YOUR_CHAT_ID,
            question=f"What is the meaning of '{word}'?",
            options=options,
            type=Poll.QUIZ,
            correct_option_id=options.index(correct)
        )


async def send_concept(app):
    bot = app.bot
    state = load_state()
    full_text = fetch_doc_text()
    concepts = split_concepts(full_text)

    idx = get_today_concept_index(len(concepts), state["start_date"])
    concept = concepts[idx]

    await bot.send_message(YOUR_CHAT_ID, f"🧠 Today’s Concept\n\n{concept}")


async def start_cmd(update, context):
    await update.message.reply_text(
        "Bot is active.\nQuiz at 8:00 PM\nConcept at 8:05 PM\n\nCommands:\n/quiz_now\n/concept_now"
    )


async def quiz_now(update, context):
    await send_quiz(context.application)


async def concept_now(update, context):
    await send_concept(context.application)


def main():
    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("quiz_now", quiz_now))
    app.add_handler(CommandHandler("concept_now", concept_now))

    scheduler = AsyncIOScheduler(timezone=IST)
    scheduler.add_job(send_quiz, CronTrigger(hour=20, minute=0), args=[app])
    scheduler.add_job(send_concept, CronTrigger(hour=20, minute=5), args=[app])
    scheduler.start()

    print("Bot running…")
    app.run_polling()


if __name__ == "__main__":
    main()
