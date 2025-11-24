import os
import requests
from datetime import datetime
from dotenv import load_dotenv

load_dotenv(".env.local")

NOTION_TOKEN = os.getenv("NOTION_TOKEN")
DATABASE_ID = os.getenv("NOTION_DATABASE_ID")

headers = {
    "Authorization": f"Bearer {NOTION_TOKEN}",
    "Content-Type": "application/json",
    "Notion-Version": "2022-06-28"
}

def add_notion_entry(mood: str, energy: str, goals: list[str]):
    url = "https://api.notion.com/v1/pages"
    
    payload = {
        "parent": { "database_id": DATABASE_ID },
        "properties": {
            "Name": {
                "title": [
                    {"text": {"content": f"Wellness Check - {datetime.now().strftime('%b %d')}"}}
                ]
            },
            "Date": {
                "date": {"start": datetime.now().isoformat()}
            },
            "Mood": {
                "rich_text": [
                    {"text": {"content": mood}}
                ]
            },
            "Energy": {
                "rich_text": [
                    {"text": {"content": energy}}
                ]
            },
            "Goals": {
                "rich_text": [
                    {"text": {"content": ", ".join(goals)}}  
                ]
            }
        }
    }

    response = requests.post(url, headers=headers, json=payload)
    return response.status_code, response.text
