import os
import httpx

GEMINI_API_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"

async def generate_test_cases(input_text: str, model: str = "gemini-1.5-pro") -> str:
    """
    Calls the Gemini API to generate test cases based on the provided input text.
    Uses the strict QA Engineer prompt to enforce JSON output.
    """
    from app.generation.prompt import QA_ENGINEER_PROMPT
    
    api_key = os.environ.get("LLM_API_KEY")
    if not api_key:
        raise ValueError("LLM_API_KEY environment variable not set")
        
    url = GEMINI_API_URL.format(model=model, api_key=api_key)
    
    # We combine the system prompt and the user input.
    # Note: Gemini 1.5 supports system_instruction, but we can also just prepend it to the text part
    # for simplicity across different model versions.
    payload = {
        "system_instruction": {
            "parts": [{"text": QA_ENGINEER_PROMPT}]
        },
        "contents": [
            {
                "role": "user",
                "parts": [
                    {"text": f"Please generate test cases for the following sections:\n\n{input_text}"}
                ]
            }
        ],
        "generationConfig": {
            "temperature": 0.2,
            "responseMimeType": "application/json"
        }
    }
    
    async with httpx.AsyncClient() as client:
        response = await client.post(url, json=payload, timeout=60.0)
        
        if response.status_code != 200:
            raise RuntimeError(f"LLM API error ({response.status_code}): {response.text}")
            
        data = response.json()
        
        try:
            return data["candidates"][0]["content"]["parts"][0]["text"]
        except (KeyError, IndexError) as e:
            raise RuntimeError(f"Unexpected response format from Gemini: {data}") from e

async def generate_test_cases_retry(input_text: str, error_msg: str, bad_output: str, model: str = "gemini-1.5-pro") -> str:
    """
    Follow-up call to repair malformed JSON.
    """
    from app.generation.prompt import QA_ENGINEER_PROMPT
    
    api_key = os.environ.get("LLM_API_KEY")
    if not api_key:
        raise ValueError("LLM_API_KEY environment variable not set")
        
    url = GEMINI_API_URL.format(model=model, api_key=api_key)
    
    payload = {
        "system_instruction": {
            "parts": [{"text": QA_ENGINEER_PROMPT}]
        },
        "contents": [
            {
                "role": "user",
                "parts": [
                    {"text": f"Please generate test cases for the following sections:\n\n{input_text}"}
                ]
            },
            {
                "role": "model",
                "parts": [{"text": bad_output}]
            },
            {
                "role": "user",
                "parts": [
                    {"text": f"Your previous response was invalid. Please provide ONLY valid JSON. Error: {error_msg}"}
                ]
            }
        ],
        "generationConfig": {
            "temperature": 0.1,
            "responseMimeType": "application/json"
        }
    }
    
    async with httpx.AsyncClient() as client:
        response = await client.post(url, json=payload, timeout=60.0)
        
        if response.status_code != 200:
            raise RuntimeError(f"LLM API error during retry ({response.status_code}): {response.text}")
            
        data = response.json()
        
        try:
            return data["candidates"][0]["content"]["parts"][0]["text"]
        except (KeyError, IndexError) as e:
            raise RuntimeError(f"Unexpected response format from Gemini during retry: {data}") from e
