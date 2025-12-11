import spotipy
from spotipy.oauth2 import SpotifyClientCredentials
import os
import requests # YouTube araması için kullanılacak

# --- GÜVENLİĞİ SAĞLAMAK İÇİN KEYLERİ ORTAM DEĞİŞKENLERİNDEN OKU ---
SPOTIFY_CLIENT_ID = os.environ.get("SPOTIFY_CLIENT_ID")
SPOTIFY_CLIENT_SECRET = os.environ.get("SPOTIFY_CLIENT_SECRET")
YT_KEY = os.environ.get("YT_KEY") 

# Supabase değişkenleri şu an kullanılmıyor ama onları da alalım
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")

def spotify_playlist_parcala(playlist_url):
    """Verilen Spotify Playlist URL'sinden tüm şarkıların listesini çeker."""
    
    if not SPOTIFY_CLIENT_ID or not SPOTIFY_CLIENT_SECRET:
        raise ValueError("Spotify kimlik bilgileri (CLIENT_ID veya SECRET) ortam değişkenlerinden okunamadı.")
    
    # API kimlik doğrulama ayarları
    # client_credentials: Bir kullanıcı girişi olmadan genel verilere erişim için kullanılır.
    auth_manager = SpotifyClientCredentials(
        client_id=SPOTIFY_CLIENT_ID,
        client_secret=SPOTIFY_CLIENT_SECRET
    )
    sp = spotipy.Spotify(auth_manager=auth_manager)

    # Playlist ID'sini URL'den çıkar
    try:
        playlist_id = playlist_url.split('/')[-1].split('?')[0]
    except Exception:
        raise ValueError("Geçersiz Playlist URL formatı.")

    sarki_listesi = []
    
    # Spotify API ile tüm şarkıları çek
    results = sp.playlist_items(playlist_id, fields='items.track(name,artists.name)', limit=50) 
    
    # Not: Daha büyük playlist'ler için 'next' alanını kontrol eden bir döngü gerekir.

    for item in results['items']:
        track = item.get('track')
        if track:
            sanatci = track['artists'][0]['name'] if track['artists'] else "Bilinmeyen Sanatçı"
            sarki_adi = track['name']
            
            # YouTube araması için sorgu oluştur
            arama_sorgusu = f"{sanatci} - {sarki_adi}"
            
            sarki_listesi.append({
                'sanatci': sanatci,
                'sarki_adi': sarki_adi,
                'arama_sorgusu': arama_sorgusu
            })
            
    return sarki_listesi

def youtube_video_ara(sorgu):
    """YouTube Data API kullanarak verilen sorgu için en alakalı video ID'sini bulur."""
    
    if not YT_KEY:
        raise ValueError("YouTube API Key (YT_KEY) ortam değişkenlerinden okunamadı.")
        
    API_URL = "https://www.googleapis.com/youtube/v3/search"
    
    params = {
        'part': 'snippet',
        'q': sorgu,
        'key': YT_KEY,
        'type': 'video',
        'maxResults': 1
    }
    
    try:
        response = requests.get(API_URL, params=params)
        response.raise_for_status() # Hata durumunda istisna fırlatır
        data = response.json()
        
        if data.get('items'):
            video_id = data['items'][0]['id']['videoId']
            return f"https://www.youtube.com/watch?v={video_id}"
        else:
            return "BULUNAMADI"
            
    except requests.exceptions.RequestException as e:
        print(f"YouTube API Hatası: {e}")
        return "API_HATASI"


# --- KULLANIM VE TEST ---
if __name__ == "__main__":
    
    # ⚠️ BURAYA TEST İÇİN GEÇERLİ BİR GENEL SPOTIFY PLAYLIST URL'Sİ GİRMELİSİNİZ
    TEST_URL = "https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M" # Örnek: Popüler Şarkılar
    
    try:
        sarkilar = spotify_playlist_parcala(TEST_URL)
        print(f"✅ Playlist'ten {len(sarkilar)} şarkı çekildi. Şimdi YouTube'da arama yapılıyor...")
        
        for i, sarki in enumerate(sarkilar[:5]): # İlk 5 şarkıyı test et
            
            youtube_url = youtube_video_ara(sarki['arama_sorgusu'])
            print(f"\n[{i+1}. Şarkı]")
            print(f"  Spotify Sorgusu: {sarki['arama_sorgusu']}")
            print(f"  YouTube URL'si: {youtube_url}")
            
    except ValueError as e:
        print(f"\nKRİTİK HATA: {e}")
    except Exception as e:
        print(f"\nBeklenmeyen Hata: {e}")
