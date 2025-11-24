import logging
import json
import os
import requests
from datetime import datetime
from typing import List, Dict, Any
from dotenv import load_dotenv
from livekit.agents import (
    Agent,
    AgentSession,
    JobContext,
    JobProcess,
    MetricsCollectedEvent,
    RoomInputOptions,
    WorkerOptions,
    cli,
    metrics,
    tokenize,
    function_tool,
    RunContext,
)
from livekit.plugins import murf, silero, google, deepgram, noise_cancellation
from livekit.plugins.turn_detector.multilingual import MultilingualModel

from prompt import SYSTEM_PROMPT

logger = logging.getLogger("agent")

load_dotenv(".env.local")

# Notion configuration
NOTION_TOKEN = os.getenv("NOTION_TOKEN")
DATABASE_ID = os.getenv("NOTION_DATABASE_ID")

NOTION_HEADERS = {
    "Authorization": f"Bearer {NOTION_TOKEN}",
    "Content-Type": "application/json",
    "Notion-Version": "2022-06-28"
}


class Assistant(Agent):
    def __init__(self) -> None:
        super().__init__(
            instructions=SYSTEM_PROMPT,
        )

    @function_tool
    async def read_wellness_log(self, context: RunContext) -> str:
        """Read the previous wellness check-in log from JSON file.
        
        Use this at the start of a session to reference past moods/goals.
        
        Returns:
            The full JSON content as a string, or 'No previous log found.' if file doesn't exist.
        """
        try:
            with open('wellness_log.json', 'r') as f:
                log = json.load(f)
            return json.dumps(log, indent=2)
        except FileNotFoundError:
            return "No previous log found."

    @function_tool
    async def write_wellness_log(self, context: RunContext, mood: str = "unspecified", objectives: List[str] = None, summary: str = "Quick check-in completed.") -> str:
        """Write a new wellness check-in entry to the JSON file.
        
        Call this right after recap, even in short sessions.
        
        Args:
            mood: User's self-reported mood/energy (e.g., "7/10, steady but a bit stressed").
            objectives: Array of 1-3 goals (e.g., ["Walk for 10 min", "Finish report"]; defaults to ["none shared"]).
            summary: Your one-sentence agent reflection (e.g., "Great start—small steps will build momentum!").
        
        Returns:
            Confirmation message like 'Log saved successfully.'
        """
        if objectives is None:
            objectives = ["none shared"]
        entry = {
            "date": datetime.now().isoformat(),
            "mood": mood,
            "objectives": objectives,
            "summary": summary
        }
        logger.info(f"Saving wellness log entry: {entry}")
        try:
            try:
                with open('wellness_log.json', 'r') as f:
                    log = json.load(f)
            except FileNotFoundError:
                log = []
            log.append(entry)
            with open('wellness_log.json', 'w') as f:
                json.dump(log, f, indent=2)
            logger.info("Wellness log saved successfully.")
            return "Log saved successfully."
        except Exception as e:
            logger.error(f"Error writing log: {e}")
            return "Sorry, couldn't save the log this time."

    @function_tool
    async def write_notion_entry(self, context: RunContext, mood: str = "unspecified", energy: str = "unspecified", goals: List[str] = None) -> str:
        """Write a wellness check-in entry to Notion database.
        
        Use this to save structured wellness data to Notion for tracking and visualization.
        Call this alongside write_wellness_log for dual logging.
        
        Args:
            mood: User's mood description (e.g., "calm", "7/10 - optimistic").
            energy: User's energy level (e.g., "medium", "6/10").
            goals: List of 1-3 daily goals/objectives.
        
        Returns:
            Confirmation message or error description.
        """
        if goals is None:
            goals = ["none shared"]
        
        if not NOTION_TOKEN or not DATABASE_ID:
            logger.warning("Notion credentials not configured")
            return "Notion logging not configured. Check environment variables."
        
        url = "https://api.notion.com/v1/pages"
        
        payload = {
            "parent": {"database_id": DATABASE_ID},
            "properties": {
                "Name": {
                    "title": [
                        {"text": {"content": f"Wellness Check - {datetime.now().strftime('%b %d, %Y')}"}}
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
        
        try:
            response = requests.post(url, headers=NOTION_HEADERS, json=payload, timeout=10)
            
            if response.status_code == 200:
                logger.info("Notion entry created successfully")
                return "Notion entry saved successfully."
            else:
                logger.error(f"Notion API error: {response.status_code} - {response.text}")
                return f"Notion save failed: {response.status_code}"
        
        except requests.exceptions.RequestException as e:
            logger.error(f"Notion request error: {e}")
            return "Couldn't reach Notion. Check your connection."
        except Exception as e:
            logger.error(f"Unexpected error saving to Notion: {e}")
            return "Error saving to Notion."


def prewarm(proc: JobProcess):
    proc.userdata["vad"] = silero.VAD.load()


async def entrypoint(ctx: JobContext):
    # Logging setup
    ctx.log_context_fields = {
        "room": ctx.room.name,
    }

    # Set up a voice AI pipeline using OpenAI, Cartesia, AssemblyAI, and the LiveKit turn detector
    session = AgentSession(
        # Speech-to-text (STT) is your agent's ears, turning the user's speech into text that the LLM can understand
        # See all available models at https://docs.livekit.io/agents/models/stt/
        stt=deepgram.STT(model="nova-3"),
        # A Large Language Model (LLM) is your agent's brain, processing user input and generating a response
        # See all available models at https://docs.livekit.io/agents/models/llm/
        llm=google.LLM(
                model="gemini-2.5-flash",
            ),
        # Text-to-speech (TTS) is your agent's voice, turning the LLM's text into speech that the user can hear
        # See all available models as well as voice selections at https://docs.livekit.io/agents/models/tts/
        tts=murf.TTS(
                voice="en-US-matthew", 
                style="Conversation",
                tokenizer=tokenize.basic.SentenceTokenizer(min_sentence_len=2),
                text_pacing=True
            ),
        # VAD and turn detection are used to determine when the user is speaking and when the agent should respond
        # See more at https://docs.livekit.io/agents/build/turns
        turn_detection=MultilingualModel(),
        vad=ctx.proc.userdata["vad"],
        # allow the LLM to generate a response while waiting for the end of turn
        # See more at https://docs.livekit.io/agents/build/audio/#preemptive-generation
        preemptive_generation=True,
    )

    # Metrics collection, to measure pipeline performance
    # For more information, see https://docs.livekit.io/agents/build/metrics/
    usage_collector = metrics.UsageCollector()

    @session.on("metrics_collected")
    def _on_metrics_collected(ev: MetricsCollectedEvent):
        metrics.log_metrics(ev.metrics)
        usage_collector.collect(ev.metrics)

    async def log_usage():
        summary = usage_collector.get_summary()
        logger.info(f"Usage: {summary}")

    ctx.add_shutdown_callback(log_usage)

    # Start the session, which initializes the voice pipeline and warms up the models
    await session.start(
        agent=Assistant(),
        room=ctx.room,
        room_input_options=RoomInputOptions(
            # For telephony applications, use `BVCTelephony` for best results
            noise_cancellation=noise_cancellation.BVC(),
        ),
    )

    # Join the room and connect to the user
    await ctx.connect()


if __name__ == "__main__":
    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint, prewarm_fnc=prewarm))
