import os
import tempfile
from urllib.request import Request, urlopen
import yt_dlp
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

auth_token = os.environ["X_AUTH_TOKEN"]
ct0 = os.environ["X_CT0"]

target_url = "https://x.com/i/status/2026275695866048623"

# Automatically resolve redirects (e.g., from /i/status/... to the actual status URL)
# We pass your auth cookies to ensure X doesn't redirect to a login/guest block page
print(f"Resolving URL: {target_url}")
req = Request(target_url, headers={
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)',
    'Cookie': f"auth_token={auth_token}; ct0={ct0}"
})
with urlopen(req) as response:
    target_url = response.geturl()
print(f"Resolved to: {target_url}")

# Dynamically generate the Netscape HTTP Cookie File content
cookie_content = f"""# Netscape HTTP Cookie File
.x.com\tTRUE\t/\tTRUE\t2000000000\tauth_token\t{auth_token}
.x.com\tTRUE\t/\tTRUE\t2000000000\tct0\t{ct0}
"""

# Create a secure, temporary file to pass to yt-dlp
with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.txt', encoding='utf-8') as temp_cookie:
    temp_cookie.write(cookie_content)
    temp_cookie_path = temp_cookie.name

print("Authenticating with temporary cookie file...")

download_dir = "/desktop/Sync/Whisper/X"
os.makedirs(download_dir, exist_ok=True)

# Set up yt-dlp options (matching your CLI arguments)
ydl_opts = {
    'cookiefile': temp_cookie_path,
    'format': 'http-2176/bestvideo+bestaudio/best', # Fallback added just in case
    'merge_output_format': 'mp4',
    'outtmpl': os.path.join(download_dir, '%(uploader)s_%(id)s.%(ext)s'),
    'prefer_free_formats': False, # Ensures it sticks to standard mp4/m4a,
    'quiet': False
}

with yt_dlp.YoutubeDL(ydl_opts) as ydl:
    ydl.download([target_url])
os.remove(temp_cookie_path)
