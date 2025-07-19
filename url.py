import socket
import ssl
import os
import PyPDF2
import io

class URL:
    def __init__(self, url):
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
        
        # HTTP/HTTPS handling
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
            "Connection": "close",
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

        response_headers = {}
        while True:
            line = response.readline()
            if line == "\r\n": break
            header, value = line.split(":", 1)
            response_headers[header.casefold()] = value.strip()

        assert "transfer-encoding" not in response_headers
        assert "content-encoding" not in response_headers

        content = response.read()
        s.close()

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


def load(url):
    body = url.request()
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