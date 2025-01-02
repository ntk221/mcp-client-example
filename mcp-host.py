"""MCP Host with support for multiple servers."""
import asyncio
import sys
from typing import Dict, List, Optional, Any, TypedDict
from contextlib import AsyncExitStack
import os

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client, get_default_environment
from mcp.types import Tool, CallToolResult

from anthropic import Anthropic, AsyncAnthropic
from dotenv import load_dotenv

load_dotenv()  # load environment variables from .env

# Check if API key is set
if not os.getenv("ANTHROPIC_API_KEY"):
    print("Warning: ANTHROPIC_API_KEY is not set in environment variables")

class Transport:
    """Manages the transport layer with a server."""
    
    def __init__(self, command: str, args: list[str], env: dict[str, str] | None = None):
        """Initialize transport.
        
        Args:
            command: The command to run the server
            args: Command line arguments
            env: Optional environment variables
        """
        self.command = command
        self.args = args
        self.env = env or get_default_environment()
        self.exit_stack = AsyncExitStack()
        self.stdio = None
        self.write = None
        
    async def connect(self) -> tuple[Any, Any]:
        """Establish transport connection with the server.
        
        Returns:
            Tuple of (stdio, write) interfaces
            
        Raises:
            ConnectionError: If connection fails
        """
        try:
            server_params = StdioServerParameters(
                command=self.command,
                args=self.args,
                env=self.env
            )
            
            # Create transport
            stdio_transport = await self.exit_stack.enter_async_context(
                stdio_client(server_params))
            self.stdio, self.write = stdio_transport
            return self.stdio, self.write
            
        except Exception as e:
            await self.cleanup()
            raise ConnectionError(f"Failed to connect to server: {str(e)}")
            
    async def cleanup(self) -> None:
        """Clean up transport resources."""
        self.stdio = None
        self.write = None
        await self.exit_stack.aclose()

class Client:
    """Represents a client that maintains a session with a server."""
    
    def __init__(self, server_name: str, transport: Transport):
        """Initialize client.
        
        Args:
            server_name: Unique identifier for this client's server
            transport: Transport instance to use
        """
        self.server_name = server_name
        self.transport = transport
        self.session: Optional[ClientSession] = None
        self._available_tools: list[Tool] = []
        self._reconnect_lock = asyncio.Lock()
        self._is_stopping = False
        
    @property
    def is_connected(self) -> bool:
        """Check if client has an active session."""
        return self.session is not None and not self._is_stopping
        
    @property
    def available_tools(self) -> list[Tool]:
        """Get list of available tools from this client's server."""
        return self._available_tools
        
    async def start(self) -> None:
        """Start the client session.
        
        Raises:
            ConnectionError: If session initialization fails
        """
        if self.is_connected:
            return
            
        async with self._reconnect_lock:
            try:
                # Establish transport connection
                stdio, write = await self.transport.connect()
                
                # Create and initialize session
                self.session = await ClientSession(stdio, write).__aenter__()
                await self.session.initialize()
                
                # Update available tools
                response = await self.session.list_tools()
                self._available_tools = response.tools
                
            except Exception as e:
                await self.cleanup()
                raise ConnectionError(
                    f"Failed to initialize session with server {self.server_name}: {str(e)}")
                    
    async def ensure_connected(self) -> None:
        """Ensure client has an active session, attempting reconnection if necessary."""
        if not self.is_connected:
            await self.start()
            
    async def stop(self) -> None:
        """Stop the client session gracefully."""
        self._is_stopping = True
        await self.cleanup()
        self._is_stopping = False
        
    async def cleanup(self) -> None:
        """Clean up client session resources."""
        self._available_tools = []
        if self.session:
            await self.session.__aexit__(None, None, None)
            self.session = None
        await self.transport.cleanup()
        
    async def call_tool(self, tool_name: str, tool_args: dict) -> CallToolResult:
        """Call a tool through this client's session.
        
        Args:
            tool_name: Name of the tool to call
            tool_args: Arguments to pass to the tool
            
        Returns:
            Tool execution result
            
        Raises:
            ConnectionError: If session is not active
            ValueError: If tool is not available
        """
        await self.ensure_connected()
        
        if not any(tool.name == tool_name for tool in self._available_tools):
            raise ValueError(
                f"Tool {tool_name} not available on server {self.server_name}")
                
        try:
            return await self.session.call_tool(tool_name, tool_args)
        except Exception as e:
            # If connection error, attempt reconnection
            if isinstance(e, ConnectionError):
                await self.cleanup()
                await self.ensure_connected()
                return await self.session.call_tool(tool_name, tool_args)
            raise

class Server:
    """Represents a server in the MCP architecture."""
    
    def __init__(self, name: str, command: str, args: list[str]):
        """Initialize server.
        
        Args:
            name: Unique identifier for this server
            command: Command to run the server
            args: Command line arguments
        """
        self.name = name
        self.command = command
        self.args = args

class McpConnection(TypedDict):
    """Represents a complete MCP connection."""
    server: Server
    client: Client
    transport: Transport

class ConnectionManager:
    """Manages multiple MCP connections."""
    
    def __init__(self):
        """Initialize connection manager."""
        self.connections: Dict[str, McpConnection] = {}
        self._lock = asyncio.Lock()
        
    async def add_connection(
        self, 
        server_name: str, 
        command: str, 
        args: list[str], 
        env: dict[str, str] | None = None
    ) -> None:
        """Add and establish a new connection.
        
        Args:
            server_name: Unique name for the server
            command: Command to run the server
            args: Command line arguments
            env: Optional environment variables
            
        Raises:
            ValueError: If server name already exists
        """
        async with self._lock:
            if server_name in self.connections:
                raise ValueError(f"Connection to {server_name} already exists")
                
            # Create server, transport and client
            server = Server(server_name, command, args)
            transport = Transport(command, args, env)
            client = Client(server_name, transport)
            
            # Initialize client
            await client.start()
            
            # Store connection
            self.connections[server_name] = {
                "server": server,
                "client": client,
                "transport": transport
            }
            
            print(f"\nConnected to server '{server_name}' with tools:")
            for tool in client.available_tools:
                print(f"  - {tool.name}: {tool.description}")
                  
    async def remove_connection(self, server_name: str) -> None:
        """Remove and close a connection.
        
        Args:
            server_name: Name of server to disconnect
            
        Raises:
            ValueError: If connection doesn't exist
        """
        async with self._lock:
            if server_name not in self.connections:
                raise ValueError(f"Connection to {server_name} does not exist")
                
            await self.connections[server_name]["client"].stop()
            del self.connections[server_name]
            
    def get_client(self, server_name: str) -> Client:
        """Get a client by server name.
        
        Args:
            server_name: Name of server to get client for
            
        Returns:
            Client instance
            
        Raises:
            ValueError: If connection doesn't exist
        """
        if server_name not in self.connections:
            raise ValueError(f"Connection to {server_name} does not exist")
        return self.connections[server_name]["client"]
        
    def get_all_tools(self) -> Dict[str, List[dict]]:
        """Get tools from all connected servers.
        
        Returns:
            Dictionary mapping server names to their tools
        """
        server_tools = {}
        for server_name, connection in self.connections.items():
            tools = [{
                "name": tool.name,
                "description": tool.description,
                "input_schema": tool.inputSchema
            } for tool in connection["client"].available_tools]
            server_tools[server_name] = tools
        return server_tools
        
    async def cleanup(self) -> None:
        """Clean up all connections."""
        async with self._lock:
            for connection in self.connections.values():
                await connection["client"].stop()
            self.connections.clear()

class LLMManager:
    """Manages interactions with the LLM."""
    
    def __init__(self, connection_manager: ConnectionManager):
        """Initialize LLM manager.
        
        Args:
            connection_manager: ConnectionManager instance to use
        """
        self.connection_manager = connection_manager
        self.anthropic = AsyncAnthropic()
        
    def _format_tools_for_llm(self, server_tools: Dict[str, List[dict]]) -> List[dict]:
        """Format tools from multiple servers for LLM consumption.
        
        Args:
            server_tools: Dictionary mapping server names to their tools
            
        Returns:
            List of tool definitions for Claude API
        """
        formatted_tools = []
        for server_name, tools in server_tools.items():
            for tool in tools:
                formatted_tools.append({
                    "name": f"{server_name}_{tool['name']}",
                    "description": f"[{server_name}] {tool['description']}",
                    "input_schema": tool['input_schema']
                })
        return formatted_tools
        
    async def process_query(self, query: str) -> str:
        """Process a query using Claude and available tools.
        
        Args:
            query: User query to process
            
        Returns:
            Response text
        """
        messages = [{"role": "user", "content": query}]
        available_tools = self._format_tools_for_llm(
            self.connection_manager.get_all_tools())

        # Initial Claude API call with streaming
        stream = await self.anthropic.messages.create(
            model="claude-3-5-sonnet-20241022",
            max_tokens=1000,
            messages=messages,
            tools=available_tools,
            stream=True
        )

        tool_results = []
        final_text = []
        current_text = ""

        async for event in stream:
            if event.type == "message_start":
                continue
            elif event.type == "content_block_start":
                continue
            elif event.type == "content_block_delta":
                if event.delta.type == "text_delta":
                    current_text += event.delta.text
                    print(event.delta.text, end="", flush=True)
            elif event.type == "content_block_stop":
                if current_text:
                    final_text.append(current_text)
                    current_text = ""
            elif event.type == "message_stop":
                if current_text:
                    final_text.append(current_text)
                    current_text = ""
            elif event.type == "tool_use":
                # Parse server and tool name
                server_name, tool_name = event.tool_calls[0].name.split('_', 1)
                
                try:
                    client = self.connection_manager.get_client(server_name)
                    result = await client.call_tool(tool_name, event.tool_calls[0].parameters)
                    tool_results.append({
                        "server": server_name,
                        "tool": tool_name,
                        "result": result
                    })
                    tool_result_text = f"\n[Called {server_name}.{tool_name} with args {event.tool_calls[0].parameters}]\n"
                    final_text.append(tool_result_text)
                    print(tool_result_text, end="", flush=True)
                    
                    # Continue conversation with tool results
                    if current_text:
                        messages.append({
                            "role": "assistant",
                            "content": current_text
                        })
                    messages.append({
                        "role": "user",
                        "content": str(result.content)
                    })
                    
                    # Get next response from Claude with streaming
                    stream = await self.anthropic.messages.create(
                        model="claude-3-5-sonnet-20241022",
                        max_tokens=1000,
                        messages=messages,
                        stream=True
                    )
                    
                    async for next_event in stream:
                        if next_event.type == "content_block_delta" and next_event.delta.type == "text_delta":
                            print(next_event.delta.text, end="", flush=True)
                            current_text += next_event.delta.text
                    
                    if current_text:
                        final_text.append(current_text)
                        current_text = ""
                    
                except Exception as e:
                    error_msg = f"\nError calling {server_name}.{tool_name}: {str(e)}"
                    print(error_msg)
                    final_text.append(error_msg)

        return "\n".join(final_text)

class Host:
    """MCP host that manages multiple clients and servers."""
    
    def __init__(self):
        """Initialize MCP host."""
        self.connection_manager = ConnectionManager()
        self.llm_manager = LLMManager(self.connection_manager)
        
    async def add_server(self, server_name: str, script_path: str, env: dict[str, str] | None = None) -> None:
        """Add and connect to a new server.
        
        Args:
            server_name: Unique name for the server
            script_path: Path to the server script
            env: Optional environment variables
        """
        if script_path.endswith('.py'):
            command = "python"
            args = [script_path]
        elif script_path.endswith('.js'):
            command = "node"
            args = [script_path]
        else:
            # Treat as executable
            command = script_path
            args = []
            
        await self.connection_manager.add_connection(server_name, command, args, env)
        
    async def chat_loop(self) -> None:
        """Run an interactive chat loop."""
        print("\nMCP Host Started!")
        print("Type your queries or 'quit' to exit.")
        
        while True:
            try:
                query = input("\nQuery: ").strip()
                
                if query.lower() == 'quit':
                    break
                    
                response = await self.llm_manager.process_query(query)
                print("\n" + response)
                    
            except Exception as e:
                print(f"\nError: {str(e)}")
                
    async def cleanup(self) -> None:
        """Clean up all resources."""
        await self.connection_manager.cleanup()

async def main() -> None:
    """Main entry point."""
    if len(sys.argv) < 3:
        print("Usage: python client.py <server_name> <path_to_server_script> [env_var1=value1 ...] "
              "[<server_name_2> <path_to_server_script_2> [env_var1=value1 ...] ...]")
        print("\nExample:")
        print("python client.py server1 mcp-server1/server.py API_KEY=xyz "
              "server2 mcp-server2/server.py TOKEN=abc")
        sys.exit(1)
        
    # Parse server configs from command line
    server_configs = []
    i = 1
    while i < len(sys.argv):
        if i + 1 >= len(sys.argv):
            break
            
        server_name = sys.argv[i]
        script_path = sys.argv[i + 1]
        i += 2
        
        # Parse environment variables for this server
        env = get_default_environment()
        while i < len(sys.argv) and '=' in sys.argv[i]:
            key, value = sys.argv[i].split('=', 1)
            env[key] = value
            i += 1
            
        server_configs.append((server_name, script_path, env))
            
    host = Host()
    try:
        # Connect to all servers
        for server_name, script_path, env in server_configs:
            await host.add_server(server_name, script_path, env)
            
        await host.chat_loop()
    finally:
        await host.cleanup()

if __name__ == "__main__":
    asyncio.run(main())
