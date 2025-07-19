import socket
import ssl
import os
import PyPDF2
import io
import time
from collections import namedtuple

# Global cache to store open sockets for reuse
socket_cache = {}

# Cache entry structure
CacheEntry = namedtuple('CacheEntry', ['content', 'headers', 'timestamp', 'expires'])

# Global cache for HTTP responses
http_cache = {}

class URL:
    def __init__(self, url):
        # Handle view-source scheme
        self.view_source = False
        if url.startswith("view-source:"):
            self.view_source = True
            url = url[len("view-source:"):]
        
        if "://" in url:
            self.scheme, url = url.split("://", 1)
        else:
            # Handle special case for data URLs which use data:
            if url.startswith("data:"):
                self.scheme = "data"
                url = url[5:]  # Remove "data:" prefix
            else:
                raise ValueError("Invalid URL format")
                
        assert self.scheme in ["http", "https", "file", "data"]

        if self.scheme == "http":
            self.port = 80
        elif self.scheme == "https":
            self.port = 443
        elif self.scheme == "data":
            # For data URLs, we store the entire data part in the path
            self.path = url
            return
        # For file:// URLs, we don't need port information


        if "/" not in url:
            url = url + "/"
        self.host, url = url.split("/", 1)
        self.path = "/" + url

        if ":" in self.host:
            self.host, port = self.host.split(":", 1)
            self.port = int(port)
            
    def _read_pdf_file(self, path):
        """Read and extract text from a PDF file."""
        try:
            with open(path, 'rb') as file:
                pdf_reader = PyPDF2.PdfReader(file)
                text = []
                
                # Add PDF metadata as header
                text.append("<html><body>")
                text.append("<h1>PDF Document</h1>")
                
                if pdf_reader.metadata:
                    text.append("<h2>Document Information</h2>")
                    text.append("<ul>")
                    for key, value in pdf_reader.metadata.items():
                        if key.startswith('/') and value:
                            clean_key = key[1:]  # Remove the leading '/'
                            text.append(f"<li><strong>{clean_key}:</strong> {value}</li>")
                    text.append("</ul>")
                
                text.append(f"<p>Number of pages: {len(pdf_reader.pages)}</p>")
                text.append("<hr>")
                
                # Extract text from each page
                for i, page in enumerate(pdf_reader.pages):
                    page_text = page.extract_text()
                    if page_text:
                        text.append(f"<h3>Page {i+1}</h3>")
                        text.append("<pre>")
                        text.append(page_text)
                        text.append("</pre>")
                        text.append("<hr>")
                    else:
                        text.append(f"<h3>Page {i+1}</h3>")
                        text.append("<p>[No extractable text on this page]</p>")
                        text.append("<hr>")
                
                text.append("</body></html>")
                return "\n".join(text)
        except Exception as e:
            return f"<html><body><h1>Error Reading PDF</h1><p>{str(e)}</p></body></html>"

    def request(self):
        # Handle data: URLs
        if self.scheme == "data":
            try:
                # Parse data URL format
                if ',' not in self.path:
                    return "<html><body><h1>Error</h1><p>Invalid data URL format</p></body></html>"
                
                media_type_and_data = self.path.split(',', 1)
                media_type = media_type_and_data[0]
                data = media_type_and_data[1]
                
                # Handle base64 encoding
                is_base64 = False
                if ';base64' in media_type:
                    is_base64 = True
                    media_type = media_type.replace(';base64', '')
                
                # If no media type is specified, default to text/plain
                if not media_type:
                    media_type = 'text/plain'
                
                # For now, we'll just return the decoded content
                if is_base64:
                    import base64
                    try:
                        decoded_data = base64.b64decode(data).decode('utf-8')
                        return decoded_data
                    except:
                        return "<html><body><h1>Error</h1><p>Invalid base64 encoding</p></body></html>"
                else:
                    # Handle URL encoding
                    import urllib.parse
                    return urllib.parse.unquote(data)
            except Exception as e:
                return f"<html><body><h1>Error</h1><p>Error processing data URL: {str(e)}</p></body></html>"
                
        # Handle file:// URLs differently
        elif self.scheme == "file":
            try:
                # Check if the file is a PDF
                if self.path.lower().endswith('.pdf'):
                    return self._read_pdf_file(self.path)
                else:
                    # Regular text file handling
                    with open(self.path, "r") as f:
                        return f.read()
            except FileNotFoundError:
                return f"<html><body><h1>Error: File not found</h1><p>The file {self.path} could not be found.</p></body></html>"
            except PermissionError:
                return f"<html><body><h1>Error: Permission denied</h1><p>You don't have permission to read {self.path}.</p></body></html>"
            except Exception as e:
                return f"<html><body><h1>Error</h1><p>An error occurred: {str(e)}</p></body></html>"
        
        # Check if we have this response cached (only for GET requests to http/https URLs)
        cache_key = f"{self.scheme}://{self.host}:{self.port}{self.path}"
        if cache_key in http_cache:
            cache_entry = http_cache[cache_key]
            current_time = time.time()
            
            # Check if the cache entry is still valid
            if cache_entry.expires > current_time:
                print(f"Using cached response for {cache_key}")
                return cache_entry.content
            else:
                # Cache entry expired, remove it
                del http_cache[cache_key]
        
        # HTTP/HTTPS handling
        socket_key = f"{self.scheme}://{self.host}:{self.port}"
        
        # Check if we have a cached socket for this host
        if socket_key in socket_cache:
            try:
                # Try to reuse the cached socket
                s = socket_cache[socket_key]
                # Test if the socket is still usable
                s.settimeout(0.1)
                s.getpeername()  # Will raise an exception if socket is closed
                s.settimeout(None)  # Reset timeout
                # If we get here, the socket is still valid
                # Remove from cache since we're using it
                del socket_cache[socket_key]
            except (OSError, socket.error):
                # Socket is no longer usable
                try:
                    s.close()
                except:
                    pass  # Ignore close errors
                # Create a new socket below
                s = None
        else:
            s = None
            
        # If we don't have a valid socket, create a new one
        if not s:
            s = socket.socket(
                family=socket.AF_INET,
                type=socket.SOCK_STREAM,
                proto=socket.IPPROTO_TCP
            )
            s.connect((self.host, self.port))
            
            if self.scheme == "https":
                ctx = ssl.create_default_context()
                s = ctx.wrap_socket(s, server_hostname=self.host)

        # Define headers in a dictionary for easy addition of new headers
        request_headers = {
            "Host": self.host,
            "User-Agent": "PyBrowser/1.0"
        }
        
        # Build the request with request_headers
        request = "GET {} HTTP/1.1\r\n".format(self.path)

        for header, value in request_headers.items():
            request += "{}: {}\r\n".format(header, value)

        request += "\r\n"

        s.send(request.encode("utf8"))

        response = s.makefile("r", encoding="utf8", newline="\r\n")

        statusline = response.readline()
        version, status, explanation = statusline.split(" ", 2)
        status = int(status)

        response_headers = {}
        while True:
            line = response.readline()
            if line == "\r\n": break
            header, value = line.split(":", 1)
            response_headers[header.casefold()] = value.strip()

        # Handle redirects (status codes 301, 302, 303, 307, 308)
        if 300 <= status < 400 and status != 304:
            if "location" in response_headers:
                # Get the redirect URL
                redirect_url = response_headers["location"]
                
                # Read and discard the body to free the socket
                if "content-length" in response_headers:
                    content_length = int(response_headers["content-length"])
                    bytes_read = 0
                    while bytes_read < content_length:
                        chunk_size = min(4096, content_length - bytes_read)
                        chunk = response.read(chunk_size)
                        if not chunk:
                            break
                        bytes_read += len(chunk)
                
                # Handle relative URLs in Location header
                if redirect_url.startswith('/'):
                    # Relative URL, combine with current host and scheme
                    redirect_url = f"{self.scheme}://{self.host}{redirect_url}"
                elif not (redirect_url.startswith('http://') or 
                          redirect_url.startswith('https://') or
                          redirect_url.startswith('file://') or
                          redirect_url.startswith('data:')):
                    # URL without scheme, assume it's relative to the current path
                    base_path = '/'.join(self.path.split('/')[:-1]) + '/'
                    redirect_url = f"{self.scheme}://{self.host}{base_path}{redirect_url}"
                
                # Store socket for possible reuse if needed
                connection_header = response_headers.get("connection", "").lower()
                if connection_header != "close" and s:
                    socket_key = f"{self.scheme}://{self.host}:{self.port}"
                    socket_cache[socket_key] = s
                
                # Return a special signal to indicate redirection
                return ("redirect", redirect_url)
            
        assert "transfer-encoding" not in response_headers
        assert "content-encoding" not in response_headers

        # Read only as many bytes as specified by Content-Length
        content = ""
        if "content-length" in response_headers:
            content_length = int(response_headers["content-length"])
            content = ""
            bytes_read = 0
            
            # Read exactly content_length bytes
            while bytes_read < content_length:
                chunk = response.read(min(4096, content_length - bytes_read))
                if not chunk:
                    break  # Connection closed prematurely
                bytes_read += len(chunk)
                content += chunk
                
            # Store the socket in the cache for future use
            connection_header = response_headers.get("connection", "").lower()
            if connection_header != "close" and s:
                socket_key = f"{self.scheme}://{self.host}:{self.port}"
                socket_cache[socket_key] = s
        else:
            # If no Content-Length header, fallback to reading everything and close
            content = response.read()
            s.close()

        # Handle caching for successful GET responses (status code 200)
        if self.scheme in ["http", "https"] and status == 200:
            # Check Cache-Control header to determine if we should cache
            should_cache = True
            max_age = 3600  # Default cache time: 1 hour
            
            if "cache-control" in response_headers:
                cache_control = response_headers["cache-control"]
                
                # Don't cache if no-store is specified
                if "no-store" in cache_control:
                    should_cache = False
                
                # Set max-age if specified
                if "max-age=" in cache_control:
                    try:
                        max_age_part = [p for p in cache_control.split(',') if "max-age=" in p][0]
                        max_age = int(max_age_part.split('=')[1].strip())
                    except (ValueError, IndexError):
                        # If we can't parse max-age, use default
                        max_age = 3600
                
                # Don't cache if there are other Cache-Control directives we don't understand
                unknown_directives = ["private", "no-cache", "must-revalidate", "proxy-revalidate", 
                                    "s-maxage=", "public", "immutable", "stale-while-revalidate", 
                                    "stale-if-error"]
                for directive in unknown_directives:
                    if directive in cache_control:
                        should_cache = False
                        break
            
            # Cache the response if appropriate
            if should_cache:
                cache_key = f"{self.scheme}://{self.host}:{self.port}{self.path}"
                expires = time.time() + max_age
                http_cache[cache_key] = CacheEntry(
                    content=content,
                    headers=response_headers,
                    timestamp=time.time(),
                    expires=expires
                )
                print(f"Cached response for {cache_key} (expires in {max_age} seconds)")
        
        return content


def show(body):
    in_tag = False
    entity = ""
    in_entity = False
    
    i = 0
    while i < len(body):
        c = body[i]
        
        # Handle entities
        if in_entity:
            if c == ';':
                # Process the complete entity
                in_entity = False
                if entity == "&lt":
                    if not in_tag:
                        print("<", end='')
                elif entity == "&gt":
                    if not in_tag:
                        print(">", end='')
                else:
                    # Unknown entity, just print it as is
                    if not in_tag:
                        print(entity + ";", end='')
                entity = ""
            else:
                entity += c
        elif c == '&':
            in_entity = True
            entity = "&"
        # Regular character handling
        elif c == '<':
            in_tag = True
        elif c == '>':
            in_tag = False
        elif not in_tag:
            print(c, end='')
            
        i += 1


def load(url, redirect_count=0, original_view_source=None):
    # Remember the view-source setting from the original URL
    if redirect_count == 0:
        original_view_source = url.view_source
    elif original_view_source is not None:
        # Apply the original view-source setting to redirected URLs
        url.view_source = original_view_source
    
    # Prevent redirect loops with a maximum number of redirects
    max_redirects = 10
    if redirect_count > max_redirects:
        print(f"Error: Too many redirects (maximum: {max_redirects})")
        return
        
    # Make the request
    result = url.request()
    
    # Check if this is a redirect response
    if isinstance(result, tuple) and len(result) == 2 and result[0] == "redirect":
        redirect_url = result[1]
        print(f"Redirecting to: {redirect_url}")
        # Handle the redirect by loading the new URL
        load(URL(redirect_url), redirect_count + 1, original_view_source)
    else:
        # Regular response, show the content
        body = result
        if url.view_source:
            print(body)
        else:
            show(body)

if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        load(URL(sys.argv[1]))
    else:
        # Default file to open when no URL is provided
        # Change this path to any file you want to use for testing
        default_file = "file:///home/giorgi/pybrowser/README.md"
        print(f"No URL provided. Opening default file: {default_file}")
        load(URL(default_file))