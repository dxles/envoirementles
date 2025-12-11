import os
import threading
import uuid
from flask import Flask, render_template, request, jsonify
from supabase import create_client, Client
import subprocess
import spotipy
from spotipy.oauth2 import SpotifyClientCredentials
import requests
import zipfile
import shutil

# ENV DEĞİŞKENLERİ
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
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

# Supabase Setup
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

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
        if not safe_filename:
            safe_filename = str(uuid.uuid4())[:8]
        
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

# ARKA PLAN İŞLEMİ (CELERYsiz - Threading ile)
def toplu_indirme_gorevi(playlist_url, output_format, gorev_id):
    """Arkaplanda çalışan indirme görevi"""
    temp_dir = os.path.join("/tmp", str(gorev_id))
    os.makedirs(temp_dir, exist_ok=True)
    
    try:
        # Görev durumunu başlat - İLERLEME MUTLAKA EKLENMELİ
        supabase.table("gorevler").insert({
            "id": gorev_id,
            "durum": "BAŞLADI",
            "kaynak": playlist_url,
            "ilerleme": "0/0",
            "indirme_url": None,
            "hata_mesaji": None
        }).execute()
        
        print(f"[{gorev_id}] Playlist parsing başladı...")
        
        # Spotify playlist'i parse et
        sarki_listesi = spotify_playlist_parcala(playlist_url)
        toplam_sarki = len(sarki_listesi)
        
        if toplam_sarki == 0:
            raise Exception("Playlist'te şarkı bulunamadı")
        
        # Toplam şarkı sayısını güncelle
        supabase.table("gorevler").update({
            "ilerleme": f"0/{toplam_sarki}",
            "durum": "İŞLENİYOR"
        }).eq("id", gorev_id).execute()
        
        print(f"[{gorev_id}] {toplam_sarki} şarkı bulundu")
        
        mp3_yollari = []
        
        for i, sarki in enumerate(sarki_listesi):
            try:
                # İlerlemeyi güncelle - Her şarkıda
                current_progress = f"{i+1}/{toplam_sarki}"
                supabase.table("gorevler").update({
                    "ilerleme": current_progress,
                    "durum": "İŞLENİYOR"
                }).eq("id", gorev_id).execute()
                
                print(f"[{gorev_id}] İşleniyor ({current_progress}): {sarki['arama_sorgusu']}")
                
                # YouTube'da ara
                youtube_url = youtube_video_ara(sarki['arama_sorgusu'])
                
                if "BULUNAMADI" in youtube_url or "API_HATASI" in youtube_url:
                    print(f"[{gorev_id}] Atlandı: {sarki['arama_sorgusu']}")
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
                    print(f"[{gorev_id}] Başarılı: {os.path.basename(downloaded_file)}")
                    
            except Exception as e:
                print(f"[{gorev_id}] Şarkı hatası ({sarki['arama_sorgusu']}): {str(e)}")
                continue
        
        if not mp3_yollari:
            raise Exception("Hiçbir şarkı indirilemedi")
        
        print(f"[{gorev_id}] ZIP oluşturuluyor... ({len(mp3_yollari)} dosya)")
        
        # ZIP durumunu göster
        supabase.table("gorevler").update({
            "ilerleme": f"{len(mp3_yollari)}/{toplam_sarki}",
            "durum": "ZIP OLUŞTURULUYOR"
        }).eq("id", gorev_id).execute()
        
        # ZIP oluştur
        zip_cikti_yolu = os.path.join("/tmp", f"{gorev_id}.zip")
        with zipfile.ZipFile(zip_cikti_yolu, 'w', zipfile.ZIP_DEFLATED) as zipf:
            for mp3_yolu in mp3_yollari:
                zipf.write(mp3_yolu, os.path.basename(mp3_yolu))
        
        print(f"[{gorev_id}] Supabase'e yükleniyor...")
        
        # Upload durumu
        supabase.table("gorevler").update({
            "durum": "YÜKLENIYOR"
        }).eq("id", gorev_id).execute()
        
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
        
        print(f"[{gorev_id}] TAMAMLANDI! Link: {indirme_linki}")
        
        # Temizlik
        shutil.rmtree(temp_dir, ignore_errors=True)
        if os.path.exists(zip_cikti_yolu):
            os.remove(zip_cikti_yolu)
        
    except Exception as e:
        hata_mesaji = str(e)
        print(f"[{gorev_id}] GENEL HATA: {hata_mesaji}")
        
        supabase.table("gorevler").update({
            "durum": "HATA",
            "hata_mesaji": hata_mesaji
        }).eq("id", gorev_id).execute()
        
        if os.path.exists(temp_dir):
            shutil.rmtree(temp_dir, ignore_errors=True)

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
                body { font-family: 'Segoe UI', sans-serif; background: #050508; color: #e5e7eb; min-height: 100vh; }
                .container { max-width: 800px; margin: 0 auto; padding: 2rem; }
                h1 { font-size: 3rem; text-align: center; margin: 2rem 0; 
                     background: linear-gradient(135deg, #6366f1, #ec4899);
                     -webkit-background-clip: text; -webkit-text-fill-color: transparent; 
                     background-clip: text; }
                .subtitle { text-align: center; color: #9ca3af; margin-bottom: 3rem; font-size: 1.1rem; }
                .form-card { background: rgba(255,255,255,0.05); padding: 2rem; 
                            border-radius: 15px; border: 1px solid rgba(255,255,255,0.1); 
                            backdrop-filter: blur(10px); }
                label { display: block; margin-bottom: 0.5rem; color: #6366f1; font-weight: 600; 
                       text-transform: uppercase; font-size: 0.85rem; letter-spacing: 1px; }
                input, select { width: 100%; padding: 1rem; margin-bottom: 1.5rem;
                               background: rgba(255,255,255,0.05); border: 2px solid rgba(255,255,255,0.1);
                               border-radius: 10px; color: white; font-size: 1rem; transition: all 0.3s; }
                input:focus, select:focus { outline: none; border-color: #6366f1; 
                                            background: rgba(255,255,255,0.08); }
                button { width: 100%; padding: 1.25rem; margin-top: 1rem;
                        background: linear-gradient(135deg, #6366f1, #ec4899);
                        border: none; border-radius: 10px; color: white; font-size: 1.1rem;
                        cursor: pointer; font-weight: bold; text-transform: uppercase; 
                        letter-spacing: 1px; transition: all 0.3s; }
                button:hover { transform: translateY(-2px); box-shadow: 0 10px 30px rgba(99,102,241,0.4); }
                button:disabled { opacity: 0.5; cursor: not-allowed; transform: none; }
                .status { display: none; margin-top: 2rem; padding: 1.5rem;
                         background: rgba(99,102,241,0.1); border-radius: 10px; 
                         border: 1px solid rgba(99,102,241,0.3); animation: slideIn 0.3s ease; }
                .status.active { display: block; }
                .status.error { background: rgba(239,68,68,0.1); border-color: rgba(239,68,68,0.3); }
                .status.success { background: rgba(34,197,94,0.1); border-color: rgba(34,197,94,0.3); }
                @keyframes slideIn {
                    from { opacity: 0; transform: translateY(20px); }
                    to { opacity: 1; transform: translateY(0); }
                }
                #statusText { font-size: 1rem; margin-bottom: 1rem; color: #e5e7eb; }
                .progress { margin-top: 1rem; font-size: 1.3rem; font-weight: bold; 
                           color: #6366f1; text-align: center; }
                .download-link { margin-top: 1.5rem; text-align: center; }
                .download-link a { display: inline-block; padding: 1rem 2rem; 
                                  background: linear-gradient(135deg, #22c55e, #10b981);
                                  color: white; text-decoration: none; border-radius: 10px;
                                  font-weight: bold; transition: all 0.3s; }
                .download-link a:hover { transform: translateY(-2px); 
                                        box-shadow: 0 10px 30px rgba(34,197,94,0.4); }
                .info { margin-top: 3rem; padding: 1.5rem; background: rgba(255,255,255,0.03);
                       border-radius: 10px; border-left: 4px solid #6366f1; }
                .info h3 { color: #6366f1; margin-bottom: 0.5rem; }
                .info p { color: #9ca3af; font-size: 0.95rem; line-height: 1.6; }
            </style>
        </head>
        <body>
            <div class="container">
                <h1>NEXUS</h1>
                <p class="subtitle">Transform your Spotify playlists into downloadable music collections</p>
                
                <div class="form-card">
                    <form id="downloadForm">
                        <label for="playlistUrl">Spotify Playlist URL</label>
                        <input type="text" id="playlistUrl" name="playlist_url" 
                               placeholder="https://open.spotify.com/playlist/..." required>
                        
                        <label for="outputFormat">Output Format</label>
                        <select id="outputFormat" name="output_format">
                            <option value="mp3">MP3 (Recommended)</option>
                            <option value="m4a">M4A (Apple)</option>
                            <option value="wav">WAV (Lossless)</option>
                        </select>
                        
                        <button type="submit" id="submitBtn">🚀 Start Download</button>
                    </form>
                    
                    <div class="status" id="status">
                        <div id="statusText">Initializing...</div>
                        <div class="progress" id="progress"></div>
                        <div class="download-link" id="downloadLink"></div>
                    </div>
                </div>
                
                <div class="info">
                    <h3>ℹ️ How it works</h3>
                    <p>
                        1. Paste your Spotify playlist URL<br>
                        2. Choose your preferred audio format<br>
                        3. Click start and wait for the download to complete<br>
                        4. Download your ZIP file with all tracks
                    </p>
                </div>
            </div>
            
            <script>
                document.getElementById('downloadForm').addEventListener('submit', async (e) => {
                    e.preventDefault();
                    
                    const formData = new FormData(e.target);
                    const statusDiv = document.getElementById('status');
                    const statusText = document.getElementById('statusText');
                    const progressDiv = document.getElementById('progress');
                    const downloadLink = document.getElementById('downloadLink');
                    const submitBtn = document.getElementById('submitBtn');
                    
                    // Reset status
                    statusDiv.classList.remove('error', 'success');
                    statusDiv.classList.add('active');
                    statusText.textContent = '⏳ Processing playlist...';
                    progressDiv.textContent = '';
                    downloadLink.innerHTML = '';
                    submitBtn.disabled = true;
                    submitBtn.textContent = '⏳ Processing...';
                    
                    try {
                        const response = await fetch('/api/download/spotify', {
                            method: 'POST',
                            body: formData
                        });
                        
                        const data = await response.json();
                        
                        if (!data.success) {
                            throw new Error(data.message || 'Failed to start download');
                        }
                        
                        statusText.textContent = '✓ Download started! Tracking progress...';
                        const taskId = data.task_id;
                        
                        const checkStatus = setInterval(async () => {
                            try {
                                const statusRes = await fetch('/api/status/' + taskId);
                                const statusData = await statusRes.json();
                                
                                if (statusData.status === 'TAMAMLANDI') {
                                    clearInterval(checkStatus);
                                    statusDiv.classList.add('success');
                                    statusText.textContent = '✓ Download Complete!';
                                    progressDiv.textContent = 'Files: ' + statusData.ilerleme;
                                    downloadLink.innerHTML = '<a href="' + statusData.link + '" download>📥 Download ZIP File</a>';
                                    submitBtn.disabled = false;
                                    submitBtn.textContent = '🚀 Start Download';
                                    
                                } else if (statusData.status === 'HATA') {
                                    clearInterval(checkStatus);
                                    statusDiv.classList.add('error');
                                    statusText.textContent = '✗ Error: ' + (statusData.message || 'Unknown error');
                                    progressDiv.textContent = '';
                                    submitBtn.disabled = false;
                                    submitBtn.textContent = '🚀 Start Download';
                                    
                                } else {
                                    statusText.textContent = '⏳ Status: ' + statusData.status;
                                    progressDiv.textContent = 'Progress: ' + (statusData.ilerleme || '0/0');
                                }
                            } catch (err) {
                                console.error('Status check error:', err);
                            }
                        }, 3000);
                        
                    } catch (err) {
                        statusDiv.classList.add('error');
                        statusText.textContent = '✗ Error: ' + err.message;
                        progressDiv.textContent = '';
                        submitBtn.disabled = false;
                        submitBtn.textContent = '🚀 Start Download';
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
        
        # Validate Spotify URL
        if 'spotify.com/playlist/' not in playlist_url:
            return jsonify({"success": False, "message": "Geçersiz Spotify playlist URL'i."}), 400
        
        # Unique task ID oluştur
        task_id = str(uuid.uuid4())
        
        # Thread başlat (arkaplanda çalışır)
        thread = threading.Thread(
            target=toplu_indirme_gorevi,
            args=(playlist_url, output_format, task_id),
            daemon=True
        )
        thread.start()
        
        return jsonify({
            "success": True,
            "message": "İndirme görevi başlatıldı.",
            "task_id": task_id
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
                "status": data.get('durum', 'UNKNOWN'),
                "ilerleme": data.get('ilerleme', '0/0'),
                "link": data.get('indirme_url'),
                "message": data.get('hata_mesaji')
            })
        
        return jsonify({
            "status": "BEKLİYOR",
            "message": "Görev henüz başlamadı.",
            "ilerleme": "0/0"
        }), 404
        
    except Exception as e:
        return jsonify({
            "status": "HATA",
            "message": str(e),
            "ilerleme": "0/0"
        }), 500

# Health check endpoint
@app.route('/health')
def health():
    return jsonify({
        "status": "healthy", 
        "service": "nexus-downloader",
        "version": "1.0.0"
    }), 200

if __name__ == '__main__':
    # Production modda debug=False
    app.run(host='0.0.0.0', port=int(PORT), debug=False)
