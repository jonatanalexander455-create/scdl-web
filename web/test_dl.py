from soundcloud import SoundCloud

sc = SoundCloud()
url = "https://soundcloud.com/jumpstreetpsy/low-extender"
try:
    track = sc.resolve(url)
    print(f"Resolving Track ID: {track.id}")
    
    # Downloading track
    print("Downloading...")
    # Get available formats
    with open('test_track.mp3', 'wb+') as f:
        track.write_mp3_to(f)
    print("Done downloading!")
except Exception as e:
    import traceback
    traceback.print_exc()
