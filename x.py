import os
import tempfile
import yt_dlp
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

auth_token = os.getenv("X_AUTH_TOKEN")
ct0 = os.getenv("X_CT0")

if not auth_token or not ct0:
    raise ValueError("Missing X_AUTH_TOKEN or X_CT0 in .env file.")

# Dynamically generate the Netscape HTTP Cookie File content
# We use an arbitrary future expiration timestamp (2000000000 = May 2033)
cookie_content = f"""# Netscape HTTP Cookie File
.x.com\tTRUE\t/\tTRUE\t2000000000\tauth_token\t{auth_token}
.x.com\tTRUE\t/\tTRUE\t2000000000\tct0\t{ct0}
"""

# Create a secure, temporary file to pass to yt-dlp
with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.txt', encoding='utf-8') as temp_cookie:
    temp_cookie.write(cookie_content)
    temp_cookie_path = temp_cookie.name

print("Authenticating with temporary cookie file...")

# Set up yt-dlp options (matching your CLI arguments)
ydl_opts = {
    'cookiefile': temp_cookie_path,
    'format': 'http-2176/bestvideo+bestaudio/best', # Fallback added just in case
    'merge_output_format': 'mp4',
    'outtmpl': 'downloads/%(uploader)s_%(id)s.%(ext)s',
    'quiet': False
}

target_url = "https://x.com/haoseshequ/status/2048017384179708064"

try:
    # Initialize and run yt-dlp
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([target_url])
finally:
    # Ensure the temporary cookie file is deleted even if the download fails
    if os.path.exists(temp_cookie_path):
        os.remove(temp_cookie_path)
        print("Cleanup: Temporary cookie file securely deleted.")