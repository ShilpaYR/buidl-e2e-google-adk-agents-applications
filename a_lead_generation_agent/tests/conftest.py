import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..")))        # agent package
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "..")))  # repo root (common/)
os.environ.setdefault("GOOGLE_API_KEY", "offline-test-key")
os.environ.setdefault("GOOGLE_GENAI_USE_VERTEXAI", "FALSE")
