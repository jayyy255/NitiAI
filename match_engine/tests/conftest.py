import os

os.environ["EMBEDDER"] = "hash"
os.environ["RERANKER"] = "none"
os.environ["GEMINI_API_KEY"] = ""
os.environ["DATABASE_URL"] = os.environ.get("TEST_DATABASE_URL", "")
os.environ["MATCH_ENGINE_TOKEN"] = "test-token"
os.environ["LTR_WEIGHTS_PATH"] = "does-not-exist.json"
