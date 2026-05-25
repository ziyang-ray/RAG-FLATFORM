"""
Test script: probe the Azure OpenAI gateway to discover the actual model behind deployment "gpt-5.4".
Usage: python test_azure_openai.py
"""

import json
import os
import requests

BASE_URL = "https://apimgateway.siemens-healthineers.com"
API_KEY = "ab6b83c59c2f488e931287b66cadd124"
DEPLOYMENT = "gpt-5.4"
AZURE_API_VERSION = os.getenv("AZURE_API_VERSION", "2024-12-01-preview").strip()


def test_chat_completion():
    """Send a minimal chat completion and dump everything we can learn about the model."""
    url = f"{BASE_URL}/openai/deployments/{DEPLOYMENT}/chat/completions?api-version={AZURE_API_VERSION}"
    headers = {
        "Content-Type": "application/json",
        "api-key": API_KEY,
    }
    payload = {
        "messages": [
            {"role": "user", "content": "Reply with a single sentence: what model are you? Include your full model name."}
        ],
        "max_completion_tokens": 256,
        "temperature": 0,
    }

    print(f"=== Azure OpenAI Model Probe ===")
    print(f"URL: {url}")
    print(f"Deployment name: {DEPLOYMENT}")
    print(f"API version: {AZURE_API_VERSION}")
    print()

    res = requests.post(url, headers=headers, json=payload, timeout=60)

    print(f"HTTP Status: {res.status_code}")
    print()

    # --- Response headers ---
    interesting_headers = [
        "x-request-id",
        "x-ms-deployment-name",
        "x-ms-model",
        "openai-model",
        "x-ratelimit-limit-requests",
        "x-ratelimit-remaining-requests",
        "openai-processing-ms",
    ]
    print("--- Key Response Headers ---")
    for h in interesting_headers:
        val = res.headers.get(h)
        if val:
            print(f"  {h}: {val}")
    # Dump all x-* and openai-* headers
    print("  --- all custom headers ---")
    for k, v in sorted(res.headers.items()):
        lk = k.lower()
        if lk.startswith("x-") or lk.startswith("openai-"):
            print(f"  {k}: {v}")
    print()

    # --- Response body ---
    if res.status_code >= 300:
        print(f"ERROR body: {res.text[:2000]}")
        return

    data = res.json()
    print("--- Response Body (full) ---")
    print(json.dumps(data, indent=2, ensure_ascii=False))
    print()

    # --- Extracted info ---
    choice = (data.get("choices") or [{}])[0]
    msg = choice.get("message") or {}
    content = msg.get("content", "")
    model_from_body = data.get("model", "<not present>")
    usage = data.get("usage", {})

    print("--- Summary ---")
    print(f"  model field in response:  {model_from_body}")
    print(f"  finish_reason:            {choice.get('finish_reason')}")
    print(f"  assistant reply:          {content}")
    print(f"  usage:                    {json.dumps(usage)}")
    print()


def test_list_deployments():
    """Try the Azure deployments list endpoint (may be blocked by gateway)."""
    url = f"{BASE_URL}/openai/deployments?api-version={AZURE_API_VERSION}"
    headers = {"api-key": API_KEY}
    print("=== Try listing deployments ===")
    print(f"URL: {url}")
    res = requests.get(url, headers=headers, timeout=30)
    print(f"HTTP Status: {res.status_code}")
    if res.status_code < 300:
        data = res.json()
        for dep in data.get("data", []):
            print(f"  deployment: {dep.get('id', '?'):30s}  model: {dep.get('model', '?'):20s}  status: {dep.get('status', '?')}")
    else:
        print(f"  Body: {res.text[:500]}")
    print()


if __name__ == "__main__":
    test_chat_completion()
    test_list_deployments()
