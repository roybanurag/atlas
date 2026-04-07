"""Slack UI Handler for Atlas permissions."""

import asyncio
from typing import Any, Dict, Optional

from slack_bolt.async_app import AsyncApp


class SlackUIHandler:
    """Handles permission requests via Slack interactive messages."""
    
    def __init__(self, app: AsyncApp):
        self.app = app
        self._pending_requests: Dict[str, asyncio.Future] = {}
        
    async def request_permission(self, request: Dict[str, Any]) -> Dict[str, Any]:
        """Request permission from user via Slack Block Kit."""
        context = request.get("context", {})
        channel_id = context.get("channel_id")
        thread_ts = context.get("thread_ts")
        
        if not channel_id:
            # Cannot request permission without a channel
            print("Error: No channel_id in permission request context")
            return {"granted": False}
            
        permission = request["permission"]
        scope = request["scope"]
        description = request["description"]
        level = request.get("level", "MEDIUM")
        
        # Create unique ID for this request
        request_id = f"perm_{permission}_{scope}_{asyncio.get_event_loop().time()}"
        
        # Create future to wait for user response
        future = asyncio.get_event_loop().create_future()
        self._pending_requests[request_id] = future
        
        # Build Block Kit message
        blocks = [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": "🛡️ Permission Request",
                    "emoji": True
                }
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*Action requires permission:*\n`{permission}`"
                }
            },
            {
                "type": "section",
                "fields": [
                    {
                        "type": "mrkdwn",
                        "text": f"*Scope:*\n{scope}"
                    },
                    {
                        "type": "mrkdwn",
                        "text": f"*Level:*\n{level}"
                    }
                ]
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*Description:*\n{description}"
                }
            },
            {
                "type": "actions",
                "block_id": request_id,
                "elements": [
                    {
                        "type": "button",
                        "text": {
                            "type": "plain_text",
                            "text": "Approve",
                            "emoji": True
                        },
                        "style": "primary",
                        "action_id": "approve_permission",
                        "value": request_id
                    },
                    {
                        "type": "button",
                        "text": {
                            "type": "plain_text",
                            "text": "Approve & Remember (24h)",
                            "emoji": True
                        },
                        "action_id": "approve_remember_permission",
                        "value": request_id
                    },
                    {
                        "type": "button",
                        "text": {
                            "type": "plain_text",
                            "text": "Deny",
                            "emoji": True
                        },
                        "style": "danger",
                        "action_id": "deny_permission",
                        "value": request_id
                    }
                ]
            }
        ]
        
        # Send message
        try:
            await self.app.client.chat_postMessage(
                channel=channel_id,
                thread_ts=thread_ts,
                blocks=blocks,
                text=f"Permission requested: {permission}"
            )
            
            # Wait for response with timeout
            try:
                # 5 minute timeout for permission requests
                result = await asyncio.wait_for(future, timeout=300)
                return result
            except asyncio.TimeoutError:
                # Remove future and return denied
                self._pending_requests.pop(request_id, None)
                return {"granted": False, "reason": "Timeout"}
                
        except Exception as e:
            print(f"Error sending Slack permission request: {e}")
            self._pending_requests.pop(request_id, None)
            return {"granted": False, "reason": str(e)}

    def handle_interaction(self, action_id: str, value: str, user_id: str) -> bool:
        """Handle button click interaction.
        
        Returns:
            True if interaction was handled, False otherwise
        """
        if value not in self._pending_requests:
            return False
            
        future = self._pending_requests.pop(value)
        
        if not future.done():
            granted = action_id in ("approve_permission", "approve_remember_permission")
            duration = "day" if action_id == "approve_remember_permission" else "session"
            future.set_result({
                "granted": granted,
                "granted_by": user_id,
                "duration": duration,
            })
            return True
            
        return False
