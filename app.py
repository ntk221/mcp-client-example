import streamlit as st
import asyncio
from mcp_host import Host
import os
from dotenv import load_dotenv

load_dotenv()

# Check if API key is set
if not os.getenv("ANTHROPIC_API_KEY"):
    st.error("Warning: ANTHROPIC_API_KEY is not set in environment variables")

# Initialize session state
if "host" not in st.session_state:
    st.session_state.host = Host()
    st.session_state.messages = []

st.title("Weather Assistant")

# Server configuration
if "server_initialized" not in st.session_state:
    st.session_state.server_initialized = False

async def init_server():
    if not st.session_state.server_initialized:
        # Initialize weather server
        await st.session_state.host.add_server("weather", "../weather/src/weather/server.py")
        st.session_state.server_initialized = True

# Initialize server
if not st.session_state.server_initialized:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(init_server())

# Chat interface
user_input = st.chat_input("Ask about weather...")

if user_input:
    # Add user message to chat
    st.session_state.messages.append({"role": "user", "content": user_input})
    
    # Display all messages
    for message in st.session_state.messages:
        with st.chat_message(message["role"]):
            st.write(message["content"])
    
    # Get AI response with streaming
    with st.chat_message("assistant"):
        message_placeholder = st.empty()
        full_response = []
        
        async def process_stream():
            messages = [{"role": "user", "content": user_input}]
            available_tools = st.session_state.host.llm_manager._format_tools_for_llm(
                st.session_state.host.connection_manager.get_all_tools())

            stream = await st.session_state.host.llm_manager.anthropic.messages.create(
                model="claude-3-5-sonnet-20241022",
                max_tokens=1000,
                messages=messages,
                tools=available_tools,
                stream=True
            )

            current_text = ""
            
            async for event in stream:
                if event.type == "message_start":
                    continue
                elif event.type == "content_block_start":
                    continue
                elif event.type == "content_block_delta":
                    if event.delta.type == "text_delta":
                        current_text += event.delta.text
                        message_placeholder.markdown(current_text + "▌")
                elif event.type == "content_block_stop":
                    if current_text:
                        full_response.append(current_text)
                        current_text = ""
                elif event.type == "message_stop":
                    if current_text:
                        full_response.append(current_text)
                        current_text = ""
                elif event.type == "tool_use":
                    server_name, tool_name = event.tool_calls[0].name.split('_', 1)
                    
                    try:
                        client = st.session_state.host.connection_manager.get_client(server_name)
                        result = await client.call_tool(tool_name, event.tool_calls[0].parameters)
                        tool_result_text = f"\n[Called {server_name}.{tool_name} with args {event.tool_calls[0].parameters}]\n"
                        full_response.append(tool_result_text)
                        message_placeholder.markdown("\n".join(full_response) + "▌")
                        
                        if current_text:
                            messages.append({
                                "role": "assistant",
                                "content": current_text
                            })
                        messages.append({
                            "role": "user",
                            "content": str(result.content)
                        })
                        
                        stream = await st.session_state.host.llm_manager.anthropic.messages.create(
                            model="claude-3-5-sonnet-20241022",
                            max_tokens=1000,
                            messages=messages,
                            stream=True
                        )
                        
                        async for next_event in stream:
                            if next_event.type == "content_block_delta" and next_event.delta.type == "text_delta":
                                current_text += next_event.delta.text
                                message_placeholder.markdown("\n".join(full_response + [current_text]) + "▌")
                        
                        if current_text:
                            full_response.append(current_text)
                            current_text = ""
                        
                    except Exception as e:
                        error_msg = f"\nError calling {server_name}.{tool_name}: {str(e)}"
                        full_response.append(error_msg)
                        message_placeholder.markdown("\n".join(full_response) + "▌")

            # Final update without the cursor
            message_placeholder.markdown("\n".join(full_response))
            return "\n".join(full_response)

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        response = loop.run_until_complete(process_stream())
        st.session_state.messages.append({"role": "assistant", "content": response})

# Display chat history
if not user_input:
    for message in st.session_state.messages:
        with st.chat_message(message["role"]):
            st.write(message["content"]) 