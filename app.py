import os
from flask import Flask, render_template, request, jsonify
from celery import Celery
from supabase import create_client, Client
import subprocess
import spotipy
from spotipy.oauth2 import SpotifyClientCredentials
import requests
import zipfile
import shutil
import uuid

# ENV DEĞİŞKENLERİ
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
PORT = os.environ.get("PORT", "8080")
SPOTIFY_CLIENT_ID = os.environ.get("SPOTIFY_CLIENT_ID")
SPOTIFY_CLIENT_SECRET = os.environ.get("SPOTIFY_CLIENT_SECRET")
YT_KEY = os.environ.get("YT_KEY")

# Template ve static folder path'lerini açıkça belirt
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
template_dir = os.path.join(BASE_DIR, 'templates')
static_dir = os.path.join(BASE_DIR, 'static')

# Flask App Setup
app = Flask(__name__, 
           template_folder=template_dir, 
           static_folder=static_dir)

# Supabase, Celery Setup
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
celery_app = Celery('tasks', broker=REDIS_URL, backend=REDIS_URL)

# CORS için basit header ekle
@app.after_request
def after_request(response):
    response.headers.add('Access-Control-Allow-Origin', '*')
    response.headers.add('Access-Control-Allow-Headers', 'Content-Type')
    response.headers.add('Access-Control-Allow-Methods', 'GET,POST,OPTIONS')
    return response

# YT-DLP ve FFmpeg ile indirme fonksiyonu
def yt_dlp_ile_indir_ve_donustur(youtube_url, sarki_adi, output_format, output_dir):
    """YouTube'dan şarkıyı indirir ve belirtilen formata çevirir"""
    try:
        # Güvenli dosya adı oluştur
        safe_filename = "".join(c for c in sarki_adi if c.isalnum() or c in (' ', '-', '_')).rstrip()
        output_path = os.path.join(output_dir, f"{safe_filename}.{output_format}")
        
        # yt-dlp komutu
        command = [
            'yt-dlp',
            '-x',  # Sadece ses
            '--audio-format', output_format,
            '--audio-quality', '0',  # En iyi kalite
            '-o', output_path.replace(f'.{output_format}', '.%(ext)s'),
            '--no-playlist',
            '--quiet',
            '--no-warnings',
            youtube_url
        ]
        
        # İndirme işlemini çalıştır
        result = subprocess.run(command, capture_output=True, text=True, timeout=300)
        
        if result.returncode == 0:
            # Başarılı, dosyayı bul
            possible_extensions = ['mp3', 'm4a', 'opus', 'wav', 'webm']
            for ext in possible_extensions:
                test_path = output_path.replace(f'.{output_format}', f'.{ext}')
                if os.path.exists(test_path):
                    # Doğru formatta değilse ffmpeg ile çevir
                    if ext != output_format:
                        final_path = output_path
                        convert_cmd = [
                            'ffmpeg', '-i', test_path,
                            '-acodec', 'libmp3lame' if output_format == 'mp3' else 'copy',
                            '-q:a', '0',
                            '-y',
                            final_path
                        ]
                        subprocess.run(convert_cmd, capture_output=True, timeout=60)
                        os.remove(test_path)
                        return final_path
                    return test_path
            
            return output_path if os.path.exists(output_path) else None
        else:
            print(f"yt-dlp hata: {result.stderr}")
            return None
            
    except Exception as e:
        print(f"İndirme hatası: {str(e)}")
        return None

def spotify_playlist_parcala(playlist_url):
    """Spotify playlist'inden şarkı bilgilerini çeker"""
    try:
        auth_manager = SpotifyClientCredentials(
            client_id=SPOTIFY_CLIENT_ID,
            client_secret=SPOTIFY_CLIENT_SECRET
        )
        sp = spotipy.Spotify(auth_manager=auth_manager)
        
        # Playlist ID'sini URL'den çıkar
        playlist_id = playlist_url.split('/')[-1].split('?')[0]
        
        sarki_listesi = []
        offset = 0
        limit = 100
        
        while True:
            results = sp.playlist_items(
                playlist_id, 
                fields='items.track(name,artists.name),next',
                limit=limit,
                offset=offset
            )
            
            for item in results['items']:
                track = item.get('track')
                if track and track.get('name'):
                    sanatci = track['artists'][0]['name'] if track.get('artists') else "Unknown Artist"
                    sarki_adi = track['name']
                    
                    sarki_listesi.append({
                        'sanatci': sanatci,
                        'sarki_adi': sarki_adi,
                        'arama_sorgusu': f"{sanatci} - {sarki_adi}"
                    })
            
            # Sonraki sayfa var mı kontrol et
            if results['next']:
                offset += limit
            else:
                break
        
        return sarki_listesi
        
    except Exception as e:
        print(f"Spotify hatası: {str(e)}")
        raise ValueError(f"Spotify playlist okunamadı: {str(e)}")

def youtube_video_ara(sorgu):
    """YouTube Data API kullanarak video arar"""
    try:
        API_URL = "https://www.googleapis.com/youtube/v3/search"
        
        params = {
            'part': 'snippet',
            'q': sorgu,
            'key': YT_KEY,
            'type': 'video',
            'maxResults': 1,
            'videoCategoryId': '10'  # Müzik kategorisi
        }
        
        response = requests.get(API_URL, params=params, timeout=10)
        response.raise_for_status()
        data = response.json()
        
        if data.get('items'):
            video_id = data['items'][0]['id']['videoId']
            return f"https://www.youtube.com/watch?v={video_id}"
        else:
            return "BULUNAMADI"
            
    except Exception as e:
        print(f"YouTube API Hatası: {str(e)}")
        return "API_HATASI"

# CELERY ARKA PLAN GÖREVİ
@celery_app.task(bind=True)
def toplu_indirme_gorevi(self, playlist_url, output_format):
    gorev_id = self.request.id
    temp_dir = os.path.join("/tmp", str(gorev_id))
    os.makedirs(temp_dir, exist_ok=True)
    
    try:
        # Görev durumunu başlat
        supabase.table("gorevler").insert({
            "id": gorev_id,
            "durum": "BAŞLADI",
            "kaynak": playlist_url,
            "ilerleme": "0/???"
        }).execute()
        
        # Spotify playlist'i parse et
        sarki_listesi = spotify_playlist_parcala(playlist_url)
        toplam_sarki = len(sarki_listesi)
        
        if toplam_sarki == 0:
            raise Exception("Playlist'te şarkı bulunamadı")
        
        mp3_yollari = []
        
        for i, sarki in enumerate(sarki_listesi):
            try:
                # İlerlemeyi güncelle
                self.update_state(state='PROGRESS', meta={'current': i + 1, 'total': toplam_sarki})
                supabase.table("gorevler").update({
                    "ilerleme": f"{i+1}/{toplam_sarki}",
                    "durum": "İŞLENİYOR"
                }).eq("id", gorev_id).execute()
                
                # YouTube'da ara
                youtube_url = youtube_video_ara(sarki['arama_sorgusu'])
                
                if "BULUNAMADI" in youtube_url or "API_HATASI" in youtube_url:
                    print(f"Atlandı: {sarki['arama_sorgusu']}")
                    continue
                
                # İndir ve çevir
                downloaded_file = yt_dlp_ile_indir_ve_donustur(
                    youtube_url,
                    sarki['arama_sorgusu'],
                    output_format,
                    temp_dir
                )
                
                if downloaded_file and os.path.exists(downloaded_file):
                    mp3_yollari.append(downloaded_file)
                    
            except Exception as e:
                print(f"Şarkı işlenirken hata ({sarki['arama_sorgusu']}): {str(e)}")
                continue
        
        if not mp3_yollari:
            raise Exception("Hiçbir şarkı indirilemedi")
        
        # ZIP oluştur
        zip_cikti_yolu = os.path.join("/tmp", f"{gorev_id}.zip")
        with zipfile.ZipFile(zip_cikti_yolu, 'w', zipfile.ZIP_DEFLATED) as zipf:
            for mp3_yolu in mp3_yollari:
                zipf.write(mp3_yolu, os.path.basename(mp3_yolu))
        
        # Supabase Storage'a yükle
        file_path = f"downloads/{gorev_id}.zip"
        with open(zip_cikti_yolu, 'rb') as f:
            supabase.storage.from_("downloads").upload(file_path, f.read())
        
        # Public URL al
        indirme_linki = supabase.storage.from_("downloads").get_public_url(file_path)
        
        # Görevi tamamla
        supabase.table("gorevler").update({
            "durum": "TAMAMLANDI",
            "indirme_url": indirme_linki,
            "ilerleme": f"{len(mp3_yollari)}/{toplam_sarki}"
        }).eq("id", gorev_id).execute()
        
        # Temizlik
        shutil.rmtree(temp_dir, ignore_errors=True)
        if os.path.exists(zip_cikti_yolu):
            os.remove(zip_cikti_yolu)
        
        return {"status": "TAMAMLANDI", "link": indirme_linki}
        
    except Exception as e:
        hata_mesaji = str(e)
        print(f"Genel hata: {hata_mesaji}")
        
        supabase.table("gorevler").update({
            "durum": "HATA",
            "hata_mesaji": hata_mesaji
        }).eq("id", gorev_id).execute()
        
        if os.path.exists(temp_dir):
            shutil.rmtree(temp_dir, ignore_errors=True)
        
        return {"status": "HATA", "hata_mesaji": hata_mesaji}

# FLASK ROUTE'LAR
@app.route('/')
def index():
    # Templates klasörü yoksa HTML'i doğrudan döndür
    try:
        return render_template('index.html')
    except:
        # Fallback HTML
        return """
        <!DOCTYPE html>
        <html lang="en">
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>NEXUS - Download Your Playlists</title>
            <style>
                * { margin: 0; padding: 0; box-sizing: border-box; }
                body { font-family: 'Segoe UI', sans-serif; background: #050508; color: #e5e7eb; }
                .container { max-width: 800px; margin: 0 auto; padding: 2rem; }
                h1 { font-size: 3rem; text-align: center; margin: 2rem 0; 
                     background: linear-gradient(135deg, #6366f1, #ec4899);
                     -webkit-background-clip: text; -webkit-text-fill-color: transparent; }
                .form-card { background: rgba(255,255,255,0.05); padding: 2rem; 
                            border-radius: 15px; border: 1px solid rgba(255,255,255,0.1); }
                input, select, button { width: 100%; padding: 1rem; margin: 1rem 0;
                                       background: rgba(255,255,255,0.05); border: 2px solid rgba(255,255,255,0.1);
                                       border-radius: 10px; color: white; font-size: 1rem; }
                button { background: linear-gradient(135deg, #6366f1, #ec4899);
                        cursor: pointer; font-weight: bold; }
                button:hover { opacity: 0.9; }
                .status { display: none; margin-top: 2rem; padding: 1rem;
                         background: rgba(34,197,94,0.2); border-radius: 10px; }
            </style>
        </head>
        <body>
            <div class="container">
                <h1>NEXUS</h1>
                <div class="form-card">
                    <form id="downloadForm">
                        <input type="text" id="playlistUrl" name="playlist_url" 
                               placeholder="Spotify Playlist URL" required>
                        <select name="output_format">
                            <option value="mp3">MP3</option>
                            <option value="m4a">M4A</option>
                            <option value="wav">WAV</option>
                        </select>
                        <button type="submit">Start Download</button>
                    </form>
                    <div class="status" id="status"></div>
                </div>
            </div>
            <script>
                document.getElementById('downloadForm').addEventListener('submit', async (e) => {
                    e.preventDefault();
                    const formData = new FormData(e.target);
                    const statusDiv = document.getElementById('status');
                    statusDiv.style.display = 'block';
                    statusDiv.textContent = 'Processing...';
                    
                    try {
                        const response = await fetch('/api/download/spotify', {
                            method: 'POST',
                            body: formData
                        });
                        const data = await response.json();
                        
                        if (data.success) {
                            statusDiv.textContent = 'Download started! Task ID: ' + data.task_id;
                            
                            const checkStatus = setInterval(async () => {
                                const statusRes = await fetch('/api/status/' + data.task_id);
                                const statusData = await statusRes.json();
                                
                                if (statusData.status === 'TAMAMLANDI') {
                                    clearInterval(checkStatus);
                                    statusDiv.innerHTML = 'Complete! <a href="' + statusData.link + 
                                                         '" style="color:#22c55e">Download</a>';
                                } else {
                                    statusDiv.textContent = 'Progress: ' + statusData.ilerleme;
                                }
                            }, 2000);
                        }
                    } catch (err) {
                        statusDiv.textContent = 'Error: ' + err.message;
                    }
                });
            </script>
        </body>
        </html>
        """

@app.route('/api/download/spotify', methods=['POST'])
def handle_spotify_download():
    try:
        playlist_url = request.form.get('playlist_url')
        output_format = request.form.get('output_format', 'mp3')
        
        if not playlist_url:
            return jsonify({"success": False, "message": "Playlist URL gerekli."}), 400
        
        # Celery task'ı başlat
        task = toplu_indirme_gorevi.apply_async(args=[playlist_url, output_format])
        
        return jsonify({
            "success": True,
            "message": "İndirme görevi başlatıldı.",
            "task_id": task.id
        }), 202
        
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500

@app.route('/api/status/<task_id>', methods=['GET'])
def get_task_status(task_id):
    try:
        response = supabase.table("gorevler").select("*").eq("id", task_id).single().execute()
        data = response.data
        
        if data:
            return jsonify({
                "status": data['durum'],
                "ilerleme": data.get('ilerleme', '0/0'),
                "link": data.get('indirme_url')
            })
        
        return jsonify({
            "status": "BEKLİYOR",
            "message": "Görev henüz başlamadı."
        }), 404
        
    except Exception as e:
        return jsonify({
            "status": "HATA",
            "message": str(e)
        }), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(PORT), debug=True)
