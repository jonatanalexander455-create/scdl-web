import sys
from soundcloud import SoundCloud

def download_track(url, client_id):
    try:
        sc = SoundCloud(client_id)
        track = sc.resolve(url)
        print(f"Track resolved: {track.title}")
        
        # Download
        with open("test_track.mp3", "wb") as f:
            sc.download_track(track, f) # assuming this exists or similar method
        print("Download complete")
    except Exception as e:
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    download_track("https://soundcloud.com/jumpstreetpsy/low-extender", "FqfkxJZWPZt411KWUg3pxbwm43M6UalQ")
