# PLAN: Fix Agent Loop Amnesia & Quota Exhaustion

## Objective
Stop blowing through API quotas due to infinite tool loops and missing conversation context.

## Steps
1. **Lower Iteration Cap:** 
   Locate where `AGENT_MAX_TOOL_ITERATIONS` is set (likely `.env` or a config file) and drop it from 200 down to 20.

2. **Fix Amnesia (Context Injection):** 
   Modify `core/agent_loop.py` and the message handler to load the last ~5-10 messages from the database/memory and prepend them to the system prompt or message list. I need to wake up knowing what we were just talking about.

3. **Implement Loop Detection:** 
   Update the execution loop in `core/agent_loop.py` to track consecutive failures. If a tool fails or outputs the same error 3 times in a row, break the loop and yield back to you for manual intervention.

## Execution
Once approved, I'll read `core/agent_loop.py` and the config files, then apply the fixes sequentially.