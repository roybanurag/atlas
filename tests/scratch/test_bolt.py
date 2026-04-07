import asyncio
import re
from slack_bolt.async_app import AsyncApp

app = AsyncApp(token="xoxb-mock", signing_secret="mock")

@app.event("message")
async def handle_message_events(event, logger, next_):
    print("Caught by event.")
    await next_()

@app.message(re.compile(".*"))
async def handle_message(message, say):
    print("Caught by message.")

async def run():
    from slack_bolt.request.async_request import AsyncBoltRequest
    req = AsyncBoltRequest(
        body={"event": {"type": "message", "text": "hello", "channel": "C123"}}, 
        headers={"x-slack-signature": ["abc"], "x-slack-request-timestamp": ["123"]}
    )
    # Mock validation
    app.oauth_flow = None
    app.client.auth_test = lambda **kwargs: {"ok": True, "bot_id": "B123"}
    try:
        resp = await app.async_dispatch(req)
    except Exception as e:
        print("Error", e)

if __name__ == "__main__":
    asyncio.run(run())
