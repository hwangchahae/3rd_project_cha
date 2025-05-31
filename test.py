import openai
import os

api_key = os.environ.get("OPENAI_API_KEY")
client = openai.OpenAI(api_key=api_key)
try:
    response = client.embeddings.create(
        input="hello world",
        model="text-embedding-3-small"
    )
    print("임베딩 성공:", response.data[0].embedding[:5])
except Exception as e:
    print("임베딩 에러:", e)