import requests
import re
from bs4 import BeautifulSoup

def get_client_id():
    html = requests.get("https://soundcloud.com/").text
    soup = BeautifulSoup(html, 'html.parser')
    scripts = soup.find_all('script', src=True)
    
    js_urls = [s['src'] for s in scripts if 'a-v2.sndcdn.com/assets/' in s['src']]
    
    for url in js_urls:
        try:
            js_content = requests.get(url, timeout=5).text
            # Look for client_id pattern like: client_id:"a7Ucuq..." or [{client_id:"..."}]
            matches = re.findall(r'client_id[:=]\s*[\'"]([A-Za-z0-9_-]{20,40})[\'"]', js_content)
            if matches:
                return matches[0]
        except Exception as e:
            print(f"Error fetching {url}: {e}")
            
    return None

if __name__ == "__main__":
    cid = get_client_id()
    if cid:
        print(f"Found Client ID: {cid}")
    else:
        print("Could not find Client ID.")
