# Atlas API Gateway

The Atlas `APIGateway` is a core security component designed to isolate sensitive credentials from both the LLM and the LangChain tool executions, while enforcing user-consented permissions. By funneling external API calls through a centralized gateway, Atlas ensures that the Agent never sees raw API keys and can only access services the user has explicitly authorized.

## 1. Core Architecture

The architecture relies on four main pillars:
1. **The Agent (LLM + Tools):** Decides what information is needed and calls tools.
2. **Tools (e.g., `web_search`, `gmail`):** Construct declarative payload requests and send them to the out-of-process gateway via HTTP. For Google services, they use the `google_proxy.py` thin client.
3. **Out-of-Process API Gateway:** A standalone FastAPI server running on `localhost:18080` that intercepts these requests, checks permissions, resolves secrets, executes the actual API calls server-side, and returns the sanitized JSON response.
4. **Permission Manager:** Integrated into the Gateway, it strictly validates whether the user has consented to the requested action and scope before executing any network call.

---

## 2. Component Interactions Workflow

When the Agent decides to use a tool like Web Search, the following lifecycle occurs:

### Step A: Tool Invocation
The LLM invokes a tool (e.g., `web_search(query="climate change")`). 
Instead of looking up the `TAVILY_API_KEY` and making a request directly via the `tavily-python` SDK, the tool constructs a declarative payload and sends it over HTTP to the locally running API Gateway:
```python
# In web_search.py
gateway_url = "http://127.0.0.1:18080/v1/search"
response = httpx.post(gateway_url, json={"query": query})
```
Similarly, for Google services (Gmail, Calendar, Drive, Tasks), tools invoke the `google_call` thin client proxy from `google_proxy.py` which pushes the request to the Gateway's `/v1/google` endpoint.

### Step B: Service Resolution & Permission Check
The Gateway receives the request and looks up `"tavily"` in its **Service Registry** (`atlas/gateway/registry.py`). The registry defines the service's `base_url`, `auth_type`, `auth_key_name`, and required `permission` (e.g., `internet_access`).

Before taking any network action, the Gateway consults the **Permission Manager**:
```python
granted = await self._pm.check(config.permission, scope=config.base_url)
```
- The `PermissionManager` checks if the user has a valid, unexpired grant for `internet_access` on `https://api.tavily.com`. 
- If no grant exists, it pauses execution and requests interactive consent via the UI handler (e.g., a CLI prompt or a Slack Block Kit message).
- If denied, the Gateway aborts and returns a 403 Unauthorized error to the tool.

### Step C: Credential Injection
If permission is granted, the Gateway securely retrieves the raw API key from the local OS Keychain via the `SecretManager`.
Based on the service's `AuthType`, it injects the secret into the HTTP request:
- `AuthType.HEADER`: Adds an `X-API-Key` or similar header.
- `AuthType.BEARER`: Adds an `Authorization: Bearer <token>` header.
- `AuthType.QUERY_PARAM`: Appends `?api_key=<token>` to the URL.
- `AuthType.CONSTRUCTOR`: Injects the key directly into the JSON body payload.

*Crucially, this raw secret never leaves the `_execute` scope within the Gateway.*

### Step D: Execution & Sanitization
The Gateway executes the HTTP request (or the Google Python SDK method in the case of Google services).
Upon receiving a response, it runs the payload through a strict **Sanitizer** (`_sanitize`). The sanitizer aggressively uses regex and recursive dictionary traversal to find and redact any instances of the API key that the external service might have echoed back in the response body (replacing it with `***REDACTED***`).

The sanitized response is then returned via the HTTP response to the Tool process, which formats it as a string for the LLM.

---

## 3. Google Workspace Isolation & Binary I/O

A notable security hardening achievement in Atlas is the total isolation of Google services (Gmail, Calendar, Tasks, Drive).
1. **Zero Access:** Tools lack any integration SDKs or local credentials.
2. **Thin Proxy Client:** The tools exclusively use `atlas/tools/google_proxy.py` to communicate structurally with the Gateway (`http://127.0.0.1:18080/v1/google`).
3. **Gateway-side Execution:** The Gateway holds the `google_auth` manager and instantiates the `googleapiclient` purely server-side.
4. **Binary I/O Handling:** For operations uploading or downloading binary data from Google Drive, tools make metadata queries via the proxy, then delegate actual binary streaming natively through `atlas/gateway/_google_download.py` which executes purely inside the Gateway context, avoiding sending bloated binary chunks over the JSON proxy while maintaining credential safety.

---

## 4. The Role of the Permission Manager

The `PermissionManager` implements a **Least-Privilege** model with persistent, encrypted storage.

- **Granular Scopes:** Permissions are divided by exact action (`email_read`, `calendar_write`, `internet_access`) and bound to specific scopes (like `gmail.com` or `*.google.com`).
- **Time-bound Grants:** Users can grant permissions `once`, for the `session`, for an `hour`, a `day`, or `forever`. The manager calculates an `expires_at` timestamp and automatically purges expired grants.
- **Trust Levels & Presets:** To prevent prompt-fatigue, users can configure a "Trust Level" (low/medium) that auto-grants lower-risk requests, or apply "Presets" (like `standard` or `reader`) which bulk-grant sets of permissions upfront.
- **Context-Aware Prompting:** When a permission isn't cached, the `PermissionManager` seamlessly triggers an async UI callback containing details of the exact action and scope, waiting for the user's manual approval before unblocking the LLM execution.

## Summary

The API Gateway acts as an airtight firewall between the Agent and the outside world. 
1. **Safety:** The LLM cannot exfiltrate API keys because it never has access to them in its context window or in the tool's immediate memory.
2. **Consent:** Every outbound API call is intercepted and strictly validated against user-granted permissions, preventing the Agent from silently performing destructive or privacy-violating actions in the background.
