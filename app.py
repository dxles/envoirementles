from flask import Flask, render_template, request, jsonify
from celery import Celery
from supabase import create_client, Client
import os
import time # Sadece simülasyon için
# Import ettiğimiz diğer kütüphaneler (spotipy, requests, yt-dlp vs.) buraya gelecek

# --- 1. ENV DEĞİŞKENLERİNİ YÜKLE (Railway'den Okunacak) ---
# os.environ.get metotları Railway'deki değişkenlerinizi otomatik olarak çeker.
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0") # Railway'de Redis servisi adresi
PORT = os.environ.get("PORT", "5000")

# --- 2. SERVİS BAĞLANTILARI ---
# Supabase Bağlantısı
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# Celery Uygulaması (İşlem Kuyruğu)
celery_app = Celery(
    'tasks',
    broker=REDIS_URL,
    backend=REDIS_URL # Celery sonuçları da Redis'te tutacak
)

# Flask Uygulaması
app = Flask(__name__)
# Flask'a Celery ayarlarını yükle
celery_app.conf.update(app.config)

# --- 3. CELERY ARKA PLAN GÖREVİ (ŞARKILARI İNDİRME VE ZIP'LEME) ---
@celery_app.task(bind=True)
def toplu_indirme_gorevi(self, playlist_url, output_format):
    """
    Uzun sürecek indirme, dönüştürme ve ZIP'leme işlemini arka planda yapar.
    """
    
    # 1. GÖREVİ SUPABASE'E KAYDET (Durum Takibi İçin)
    gorev_id = self.request.id # Celery'nin verdiği benzersiz ID
    supabase.table("gorevler").insert({
        "id": gorev_id, 
        "durum": "BAŞLADI", 
        "kaynak": playlist_url,
        "ilerleme": "0/???"
    }).execute()

    try:
        # 2. SPOTIFY -> YOUTUBE LİSTESİ ÇEK
        # sarki_listesi = spotify_playlist_parcala(playlist_url) # Önceki kodumuz
        sarki_listesi = ["Sanatçı 1 - Şarkı 1", "Sanatçı 2 - Şarkı 2"] # Simülasyon
        toplam_sarki = len(sarki_listesi)

        mp3_yollari = []
        for i, sorgu in enumerate(sarki_listesi):
            # 3. YOUTUBE'DA ARATMA VE İNDİRME (yt-dlp + FFmpeg)
            
            # --- Gerçek kod burada olacak ---
            # youtube_url = youtube_video_ara(sorgu)
            # gecici_mp3_yol = yt_dlp_ile_indir_ve_donustur(youtube_url, output_format)
            # mp3_yollari.append(gecici_mp3_yol)
            
            time.sleep(1) # İşlem süresini simüle et
            
            # 4. İLERLEME GÜNCELLEMESİ
            self.update_state(state='PROGRESS', meta={'current': i + 1, 'total': toplam_sarki})
            supabase.table("gorevler").update({"ilerleme": f"{i+1}/{toplam_sarki}"}).eq("id", gorev_id).execute()


        # 5. ZIP'LEME İŞLEMİ (Önceki mesajdaki kod)
        # zip_yolu = mp3_leri_ziplama_gorevi(mp3_yollari, f"{gorev_id}.zip")
        zip_yolu_simulasyon = "/tmp/final_indirme.zip"

        # 6. SUPABASE STORAGE'A YÜKLEME
        # file_path = os.path.basename(zip_yolu_simulasyon)
        # with open(zip_yolu_simulasyon, 'rb') as f:
        #     supabase.storage.from_("downloads").upload(file_path, f.read())

        # 7. GÖREVİ TAMAMLA
        indirme_linki = f"{SUPABASE_URL}/storage/v1/object/public/downloads/final_indirme.zip" # Simülasyon
        supabase.table("gorevler").update({"durum": "TAMAMLANDI", "indirme_url": indirme_linki}).eq("id", gorev_id).execute()
        
        return {"status": "TAMAMLANDI", "link": indirme_linki}

    except Exception as e:
        supabase.table("gorevler").update({"durum": "HATA", "hata_mesaji": str(e)}).eq("id", gorev_id).execute()
        return {"status": "HATA", "hata_mesaji": str(e)}


# --- 4. FLASK API UÇ NOKTASI ---
@app.route('/api/download/spotify', methods=['POST'])
def handle_spotify_download():
    playlist_url = request.form.get('playlist_url')
    output_format = request.form.get('output_format')
    
    if not playlist_url:
        return jsonify({"success": False, "message": "Playlist URL'si gerekli."}), 400

    # CELERY İŞLEMİNİ BAŞLAT
    # İşlemi Celery'ye havale et ve anında sonuç (gorev_id) al
    task = toplu_indirme_gorevi.apply_async(args=[playlist_url, output_format])
    
    return jsonify({
        "success": True, 
        "message": "İndirme görevi başarıyla başlatıldı.",
        "task_id": task.id, # Durum takibi için bu ID'yi kullanacağız
        "status_url": f"/api/status/{task.id}"
    }), 202 # 202: Kabul edildi, işlem devam ediyor


# --- 5. DURUM KONTROL API UÇ NOKTASI ---
@app.route('/api/status/<task_id>', methods=['GET'])
def get_task_status(task_id):
    # Supabase'den görev durumunu kontrol et
    response = supabase.table("gorevler").select("*").eq("id", task_id).single().execute()
    
    if response.data:
        return jsonify({
            "status": response.data['durum'],
            "ilerleme": response.data['ilerleme'],
            "link": response.data.get('indirme_url')
        })
    return jsonify({"status": "BEKLİYOR", "message": "Görev bulunamadı veya daha başlamadı."}), 404


if __name__ == '__main__':
    # Flask sunucusu bu portta çalışacak
    app.run(host='0.0.0.0', port=int(PORT), debug=True)
