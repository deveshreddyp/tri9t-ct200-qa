import os
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

class Settings:
    LLM_API_KEY: str = os.getenv("LLM_API_KEY", "")
    LLM_PROVIDER: str = os.getenv("LLM_PROVIDER", "gemini")
    DATABASE_URL: str = os.getenv("DATABASE_URL", "sqlite:///./ct200_qa.db")

settings = Settings()
