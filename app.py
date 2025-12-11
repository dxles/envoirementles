from flask import Flask, render_template, request, jsonify, redirect, url_for
from celery import Celery
from supabase import create_client, Client
import os
import subprocess
import spotipy
from spotipy.oauth2 import SpotifyClientCredentials
import requests
import zipfile
import shutil
import uuid # Geçici klasörler için benzersiz ID üretmek için

# --- ENV DEĞİŞKENLERİ VE SERVİS BAĞLANTILARI ---
# (Önceki mesajlarda tanımlananlar)

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0") 
PORT = os.environ.get("PORT", "8080")

# Supabase, Celery, Flask Uygulaması Kurulumu (Önceki mesajdaki gibi)
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
celery_app = Celery('tasks', broker=REDIS_URL, backend=REDIS_URL)
app = Flask(__name__, template_folder='templates') # templates klasörüne bakacak

# --- YARDIMCI FONKSİYONLAR (Önceki Mesajlardan Çekildi) ---

# YT-DLP ve FFmpeg ile indirme fonksiyonu
def yt_dlp_ile_indir_ve_donustur(youtube_url, sarki_adi, output_format):
    # ... (YT-DLP ve FFmpeg komutlarını içeren fonksiyon buraya gelecek)
    # NOT: Bu, Railway ortamında yt-dlp ve ffmpeg kurulu olmasını gerektirir.
    
    # Şimdilik bir yer tutucu (placeholder) döndürelim:
    return os.path.join(f"/tmp/media_processing/{uuid.uuid4()}", f"{sarki_adi}.{output_format}") 
    
def spotify_playlist_parcala(playlist_url):
    # ... (Spotify API kullanarak playlist verilerini çeken fonksiyon buraya gelecek)
    
    # Şimdilik bir simülasyon döndürelim:
    return [{"arama_sorgusu": "The Weeknd - Blinding Lights"}, {"arama_sorgusu": "Tarkan - Şımarık"}]

def youtube_video_ara(sorgu):
    # ... (YT_KEY kullanarak YouTube'da arama yapan fonksiyon buraya gelecek)
    
    # Şimdilik bir simülasyon döndürelim:
    return f"https://www.youtube.com/watch?v=SIMULASYON_{sorgu.replace(' ', '_')}"


# --- CELERY ARKA PLAN GÖREVİ (ÇEKİRDEK İŞLEM) ---
@celery_app.task(bind=True)
def toplu_indirme_gorevi(self, playlist_url, output_format):
    gorev_id = self.request.id
    temp_dir = os.path.join("/tmp", str(gorev_id))
    os.makedirs(temp_dir, exist_ok=True)
    
    # 1. Görev Durumunu Başlat
    supabase.table("gorevler").insert({"id": gorev_id, "durum": "BAŞLADI", "kaynak": playlist_url, "ilerleme": "0/???"}).execute()

    try:
        sarki_listesi = spotify_playlist_parcala(playlist_url)
        toplam_sarki = len(sarki_listesi)
        mp3_yollari = []

        for i, sarki in enumerate(sarki_listesi):
            self.update_state(state='PROGRESS', meta={'current': i + 1, 'total': toplam_sarki})
            supabase.table("gorevler").update({"ilerleme": f"{i+1}/{toplam_sarki}", "durum": "İŞLENİYOR"}).eq("id", gorev_id).execute()

            # 2. YouTube Araması ve İndirme
            youtube_url = youtube_video_ara(sarki['arama_sorgusu'])
            
            if "BULUNAMADI" in youtube_url: continue # Şarkı bulunamazsa atla

            # NOTE: Burada yt_dlp_ile_indir_ve_donustur çağrılacak.
            # Simülasyon: Geçici bir dosya oluşturulur
            gecici_mp3_yol = os.path.join(temp_dir, f"{sarki['arama_sorgusu']}.{output_format}")
            with open(gecici_mp3_yol, 'w') as f: f.write("simülasyon içeriği")
            
            mp3_yollari.append(gecici_mp3_yol)


        # 3. ZIP'LEME İŞLEMİ
        zip_cikti_yolu = os.path.join("/tmp", f"{gorev_id}.zip")
        with zipfile.ZipFile(zip_cikti_yolu, 'w', zipfile.ZIP_DEFLATED) as zipf:
            for mp3_yolu in mp3_yollari:
                zipf.write(mp3_yolu, os.path.basename(mp3_yolu))

        # 4. SUPABASE STORAGE'A YÜKLEME (Gerçek indirme linki için)
        file_path = f"downloads/{gorev_id}.zip"
        with open(zip_cikti_yolu, 'rb') as f:
            supabase.storage.from_("downloads").upload(file_path, f.read())

        # Public URL al
        indirme_linki = supabase.storage.from_("downloads").get_public_url(file_path)
        
        # 5. Görevi Tamamla ve Linki Kaydet
        supabase.table("gorevler").update({"durum": "TAMAMLANDI", "indirme_url": indirme_linki, "ilerleme": f"{toplam_sarki}/{toplam_sarki}"}).eq("id", gorev_id).execute()
        
        # Temizlik
        shutil.rmtree(temp_dir)
        os.remove(zip_cikti_yolu) 

        return {"status": "TAMAMLANDI", "link": indirme_linki}

    except Exception as e:
        hata_mesaji = str(e)
        supabase.table("gorevler").update({"durum": "HATA", "hata_mesaji": hata_mesaji}).eq("id", gorev_id).execute()
        
        if os.path.exists(temp_dir): shutil.rmtree(temp_dir)
        return {"status": "HATA", "hata_mesaji": hata_mesaji}


# --- FLASK UÇ NOKTALARI (API) ---
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/download/spotify', methods=['POST'])
def handle_spotify_download():
    # ... (Önceki mesajdaki Flask API kodu: Celery görevini başlatır ve task_id döndürür) ...
    playlist_url = request.form.get('playlist_url')
    output_format = request.form.get('output_format')
    
    if not playlist_url:
        return jsonify({"success": False, "message": "URL gerekli."}), 400

    task = toplu_indirme_gorevi.apply_async(args=[playlist_url, output_format])
    
    return jsonify({
        "success": True, 
        "message": "İndirme görevi başlatıldı.",
        "task_id": task.id
    }), 202 

@app.route('/api/status/<task_id>', methods=['GET'])
def get_task_status(task_id):
    # ... (Önceki mesajdaki Supabase'den durum çeken API kodu) ...
    try:
        response = supabase.table("gorevler").select("*").eq("id", task_id).single().execute()
        data = response.data
        if data:
            return jsonify({
                "status": data['durum'],
                "ilerleme": data['ilerleme'],
                "link": data.get('indirme_url')
            })
        return jsonify({"status": "BEKLİYOR", "message": "Görev ID bulunamadı."}), 404
    except Exception:
        return jsonify({"status": "BEKLİYOR", "message": "Görev henüz başlamadı veya DB hatası."}), 404

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(PORT), debug=True)
