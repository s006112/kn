# test_gpt5.py
import os

from dotenv import load_dotenv
from openai import OpenAI

def main():
    load_dotenv()
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        print("ERROR: OPENAI_API_KEY environment variable not set")
        return

    client = OpenAI(api_key=api_key)

    try:
        resp = client.responses.create(
#            model="gpt-5-nano",
#            model="gpt-5-mini",
            model="gpt-5",
            input="Say the one word: ready."
        )
        print("Model used:", resp.model)
        print("Output text:", repr(resp.output_text))
        if resp.output_text.strip().lower() == "ready":
            print("✅ Success: model is accessible and responded correctly")
        else:
            print("⚠️ Model responded but not the expected word")
    except Exception as e:
        print("❌ Failed calling the model:", str(e))

if __name__ == "__main__":
    main()
