import asyncio
from langchain_core.messages import HumanMessage
from atlas.config.paths import get_data_dir
from atlas.config.model_manager import ModelManager
from atlas.graph import create_agent_graph
from atlas.tools.tools_loader import load_all_tools
from langgraph.checkpoint.memory import MemorySaver

async def run():
    model_mgr = ModelManager()
    data_dir = get_data_dir()
    tools = load_all_tools(data_dir)
    llm = model_mgr.create_llm("qwen3:14b", tools=tools)
    graph = create_agent_graph(tools=tools, checkpointer=MemorySaver())
    
    input_state = {"messages": [HumanMessage(content="What is the weather in Tokyo?")], "needs_memory_refresh": False}
    config = {"configurable": {"thread_id": "test_123", "llm": llm, "verbose": True}}
    
    async for state in graph.astream(input_state, config, stream_mode="values"):
        last_msg = state.get("messages", [])[-1]
        print("STATE MSG:", last_msg.type, getattr(last_msg, "content", ""), "TOOL CALLS:", getattr(last_msg, "tool_calls", None))
        
    messages = state.get("messages", [])
    for msg in reversed(messages):
        is_ai = getattr(msg, "__class__", None) and msg.__class__.__name__ == "AIMessage"
        if not is_ai and getattr(msg, "type", None) != "ai":
            continue
        content = getattr(msg, "content", "")
        if content:
            print("FINAL RESPONSE:", content)
            break

if __name__ == "__main__":
    asyncio.run(run())
