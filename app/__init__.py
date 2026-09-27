"""Load project configuration consistently for web and CLI on Linux/Windows."""
from pathlib import Path

from dotenv import load_dotenv

# Existing process/service environment variables always take precedence.
load_dotenv(Path(__file__).resolve().parents[1] / '.env', override=False, encoding='utf-8-sig')
