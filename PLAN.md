# PLAN.md: Enable Direct File Sending to Telegram

## Objective
Add a `send_file` tool to Anti-claw so I can send generated files (scripts, logs, docs) directly to the Telegram chat, rather than just writing them to the local workspace.

## Steps

1.  **Update `core/tools.py`**
    *   Add `send_file` to the Anthropic tools definition list (`get_anthropic_tools`).
    *   Implement the `execute_send_file` method. This method will take a `path` parameter, verify the file exists within the allowed workspace, and then return a special structured response or signal that the agent loop can interpret. *Self-correction: Since the tool registry doesn't have direct access to the `Message` object to reply, it should return a specific string token or we need to pass a callback.*
    *   *Better approach:* The tool registry just validates the file and returns a success message to the LLM. The actual sending needs to happen in the agent loop.
    *   *Revised approach for `core/tools.py`:* Add `send_file` to schema. The `execute_send_file` method will just return an absolute path to the file if it's safe and exists.

2.  **Update `core/agent_loop.py`**
    *   Modify the tool execution dispatcher to handle the `send_file` tool specifically.
    *   When the LLM calls `send_file`, the loop intercepts it. It gets the safe absolute path from `tool_registry.execute_send_file`.
    *   The loop then uses the global `bot` instance (or a passed-in reference to it) to send the document to the user associated with `task_id`. (Since `task_id` is usually `chat_<chat_id>`, we can parse the chat ID from it).
    *   We need to import `FSInputFile` from `aiogram.types`.

3.  **Verification**
    *   Restart the main process.
    *   Test by asking the agent to create a text file and send it.
