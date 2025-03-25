import asyncio
import websockets
import json
import uuid
import base64
import threading
import queue
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

# Global variables for communication
clients = {}  # Maps domain to WebSocket connection
message_queues = {}  # Maps domain to message queue for that domain
responses = {}  # Maps request_id to response data

class ProxyHTTPRequestHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.process_request()

    def do_POST(self):
        self.process_request()
        
    def process_request(self):
        hostname = self.headers.get('Host')
        if hostname == "twapi-endpoint.milosantos.com":
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"Set this URL as an CNAME record with proxy enabled. | TWAPI v2")
            return
        if not hostname or hostname not in clients:
            self.send_response(503)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"No peers for this domain.")
            return

        # Create a unique request ID
        request_id = str(uuid.uuid4())
        
        # Prepare the request data
        request_data = {
            "id": request_id,
            "content": {
                "method": self.command,
                "path": self.path,
                "headers": dict(self.headers),
            }
        }
        
        # Read request body if present
        content_length = int(self.headers.get('Content-Length', 0))
        if content_length > 0:
            request_data["content"]["body"] = self.rfile.read(content_length).decode('utf-8')

        # Put the request in the domain's message queue
        message_queues[hostname].put(request_data)
        
        # Wait for the response with timeout
        start_time = time.time()
        max_wait_time = 28  # seconds
        
        while time.time() - start_time < max_wait_time:
            if request_id in responses:
                # We got a response, process it
                response = responses[request_id]
                
                try:
                    # Send the response back to the HTTP client
                    self.send_response(response.get("statusCode", 200))
                    for header, value in response.get("headers", {}).items():
                        self.send_header(header, value)
                    self.end_headers()
                    
                    body = response.get("body", "")
                    if body:
                        if response.get("isBase64Encoded", False):
                            # Ensure it's a valid Base64 string before decoding
                            try:
                                body = base64.b64decode(body)
                            except Exception as e:
                                body = b""  # Fallback to empty bytes in case of failure
                        elif isinstance(body, str):
                            body = body.encode()  # Convert string to bytes if needed

                        # Write body to the response
                        self.wfile.write(body)
                except Exception as e:
                    print(f"Error sending response: {str(e)}")
                
                # Clean up
                del responses[request_id]
                return
            
            # Sleep a bit before checking again
            time.sleep(0.1)
        
        # If we get here, we timed out waiting for a response
        self.send_response(408)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(b"Peer took too much time to reply.")

# Function to handle large message fragmentation when sending
async def send_large_message(websocket, message):
    # Convert to JSON once
    message_json = json.dumps(message)
    
    # Maximum size for each message chunk (5GB)
    max_chunk_size = 5120 * 1024 * 1024  
    
    if len(message_json) <= max_chunk_size:
        # Message is small enough to send in one go
        await websocket.send(message_json)
    else:
        # We need to split the message
        message_chunks = [message_json[i:i+max_chunk_size] 
                         for i in range(0, len(message_json), max_chunk_size)]
        
        for chunk in message_chunks:
            await websocket.send(chunk)
            # Small delay to prevent flooding
            await asyncio.sleep(0.01)

async def process_outgoing_messages(domain, websocket):
    """Process outgoing messages for a specific domain"""
    queue = message_queues[domain]
    while True:
        try:
            # Check if we have any messages to send
            while not queue.empty():
                message = queue.get()
                await send_large_message(websocket, message)
                queue.task_done()
            
            # Wait a bit before checking again
            await asyncio.sleep(0.1)
        except Exception as e:
            print(f"Error processing outgoing messages for {domain}: {str(e)}")
            await asyncio.sleep(1)  # Wait before retrying

# Buffer to accumulate incoming message fragments
message_buffers = {}  # Maps domain to buffer string

async def websocket_handler(websocket):
    domain = None
    try:
        # Handle registration
        message = await websocket.recv()
        data = json.loads(message)
        
        if data.get("type") == "register" and "domain" in data:
            domain = data["domain"]
            message_buffers[domain] = ""  # Initialize message buffer for this domain
            
            with open('twapi.db','r') as f:
                db = json.load(f)
            if domain in db:
                if str(data["key"]) == str(db[domain]):
                    clients[domain] = websocket
                    message_queues[domain] = queue.Queue()
                    await websocket.send(json.dumps({"status": "registered", "domain": domain}))
                else:
                    await websocket.send(json.dumps({"status": "error", "message":"Invalid key.", "domain": domain}))
                    await websocket.close()
            else:
                await websocket.send(json.dumps({"status": "error", "message":"Register this domain at https://twapi-reg.milosantos.com/", "domain": domain}))
                await websocket.close()
            
            # Start processing outgoing messages
            outgoing_task = asyncio.create_task(process_outgoing_messages(domain, websocket))
            
            # Handle incoming messages (responses)
            try:
                async for message_fragment in websocket:
                    try:
                        # Try to parse as JSON directly first
                        try:
                            data = json.loads(message_fragment)
                            if "id" in data:
                                request_id = data["id"]
                                responses[request_id] = data["content"]
                            # Reset buffer if we successfully processed a complete message
                            message_buffers[domain] = ""
                        except json.JSONDecodeError:
                            # Could be a fragment of a large message
                            # Append to the buffer
                            message_buffers[domain] += message_fragment
                            
                            # Try to parse the accumulated buffer
                            try:
                                data = json.loads(message_buffers[domain])
                                if "id" in data:
                                    request_id = data["id"]
                                    responses[request_id] = data["content"]
                                # Reset buffer if we successfully processed a complete message
                                message_buffers[domain] = ""
                            except json.JSONDecodeError:
                                # Still not a complete JSON, continue accumulating
                                pass
                    except Exception as e:
                        print(f"Error processing message from {domain}: {str(e)}")
                        # Reset buffer on other errors
                        message_buffers[domain] = ""
            finally:
                # Cancel the outgoing message task when the WebSocket closes
                outgoing_task.cancel()
        else:
            # Not a registration message
            await websocket.close()
    except Exception as e:
        print(f"WebSocket error: {str(e)}")
    finally:
        # Clean up when the connection closes
        if domain:
            if domain in clients:
                del clients[domain]
            if domain in message_queues:
                del message_queues[domain]
            if domain in message_buffers:
                del message_buffers[domain]
            print(f"Client unregistered for domain: {domain}")

def run_http_server(server):
    print("HTTP server running on port 80")
    server.serve_forever()

async def main():
    # Configure WebSocket server with larger message size limits
    # Set max_size to 5GB (plus some buffer, 500MB in this case)
    max_message_size = 5120 + 500 * 1024 * 1024  # 5.5GB in bytes
    
    # Start WebSocket server with the configured max message size
    print(f"Starting WebSocket server on port 8080 with max message size of {max_message_size} bytes...")
    
    # Use only the supported parameters
    websocket_server = await websockets.serve(
        websocket_handler, 
        "", 
        8080,
        max_size=max_message_size,
        max_queue=64  # Increase queue size
    )
    
    print("WebSocket server started!")
    
    # Start HTTP server in a separate thread
    http_server = HTTPServer(("", 80), ProxyHTTPRequestHandler)
    http_thread = threading.Thread(target=run_http_server, args=(http_server,), daemon=True)
    http_thread.start()
    
    print("Server started successfully!")
    
    try:
        # Keep the main task running
        await asyncio.Future()
    except asyncio.CancelledError:
        pass
    finally:
        websocket_server.close()
        await websocket_server.wait_closed()
        # Don't call http_server.shutdown() as it can block

if __name__ == "__main__":
    # Set up and run the event loop
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    try:
        loop.run_until_complete(main())
    except KeyboardInterrupt:
        print("Server shutting down...")
    finally:
        loop.close()
