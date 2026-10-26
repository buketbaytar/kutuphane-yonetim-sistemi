#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Kütüphane Yönetim ve Ödünç Takip Sistemi
========================================
Python + SQLite (yerleşik sqlite3) ile yazılmış uçtan uca CRUD uygulaması.

Modüller:
    1. Kitap Yönetimi   : kitap_ekle, kitaplari_listele, kitap_guncelle, kitap_sil
    2. Üye Yönetimi     : uye_ekle, uyeleri_listele
    3. Ödünç / Takip    : odunc_ver, iade_al, odunc_durumu_kontrol, aktif_oduncleri_listele
    4. Raporlama        : kutuphane_durumu_goster

Çalıştırma:
    python kutuphaneaciklama.py   (menü açılır, işlemleri klavyeden yaparsınız)
"""

import os
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, timedelta

# ----------------------------------------------------------------------------
# GENEL AYARLAR
# ----------------------------------------------------------------------------
DB_ADI = "kutuphane.db"          # Veritabanı dosyasının adı
VARSAYILAN_ODUNC_GUN = 14        # Bir kitabın varsayılan ödünç süresi (gün)
TARIH_FORMATI = "%Y-%m-%d"       # Tarihler veritabanında ISO biçiminde (YYYY-AA-GG) tutulur


# ----------------------------------------------------------------------------
# ÖZEL HATA SINIFI
# ----------------------------------------------------------------------------
class KutuphaneHatasi(Exception):
    """İş kuralı ihlallerinde (stok yok, kayıt bulunamadı vb.) fırlatılan hata."""


# ----------------------------------------------------------------------------
# VERİTABANI BAĞLANTISI VE TABLO KURULUMU
# ----------------------------------------------------------------------------
@contextmanager
def baglanti():
    """
    Güvenli bir veritabanı bağlantısı açar.
    - Hata yoksa işlemleri otomatik kaydeder (commit).
    - Hata varsa işlemleri geri alır (rollback).
    - Her durumda bağlantıyı kapatır.
    """
    conn = sqlite3.connect(DB_ADI)
    conn.row_factory = sqlite3.Row            # Sütunlara isimle erişebilmek için
    conn.execute("PRAGMA foreign_keys = ON")  # SQLite'ta yabancı anahtarlar varsayılan KAPALIDIR
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def veritabani_olustur():
    """Gerekli tabloları (yoksa) oluşturur. Uygulama başlarken bir kez çağrılır."""
    with baglanti() as conn:
        # --- KİTAPLAR tablosu ---
        conn.execute("""
            CREATE TABLE IF NOT EXISTS kitaplar (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                kitap_adi      TEXT    NOT NULL,
                yazar          TEXT    NOT NULL,
                sayfa_sayisi   INTEGER NOT NULL CHECK (sayfa_sayisi > 0),
                kategori       TEXT    NOT NULL,
                toplam_stok    INTEGER NOT NULL CHECK (toplam_stok >= 0),
                eklenme_tarihi TEXT    NOT NULL DEFAULT (date('now', 'localtime')),
                UNIQUE (kitap_adi, yazar)      -- Aynı kitap iki kez eklenemesin (stok güncellenmeli)
            )
        """)

        # --- ÜYELER tablosu ---
        conn.execute("""
            CREATE TABLE IF NOT EXISTS uyeler (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                ad_soyad     TEXT NOT NULL,
                telefon      TEXT,
                eposta       TEXT UNIQUE,
                kayit_tarihi TEXT NOT NULL DEFAULT (date('now', 'localtime'))
            )
        """)

        # --- ÖDÜNÇ İŞLEMLERİ tablosu ---
        # iade_tarihi NULL ise kitap hâlâ ödünçtedir.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS odunc_islemleri (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                kitap_id          INTEGER NOT NULL,
                uye_id            INTEGER NOT NULL,
                odunc_tarihi      TEXT    NOT NULL,
                son_teslim_tarihi TEXT    NOT NULL,
                iade_tarihi       TEXT,
                FOREIGN KEY (kitap_id) REFERENCES kitaplar (id) ON DELETE CASCADE,
                FOREIGN KEY (uye_id)   REFERENCES uyeler   (id) ON DELETE RESTRICT
            )
        """)

        # Aktif ödünç sorgularını hızlandıran indeks
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_odunc_aktif
            ON odunc_islemleri (kitap_id, iade_tarihi)
        """)


# ----------------------------------------------------------------------------
# YARDIMCI FONKSİYONLAR
# ----------------------------------------------------------------------------
def _tarih_yaz(tarih):
    """date nesnesini 'YYYY-AA-GG' metnine çevirir."""
    return tarih.strftime(TARIH_FORMATI)


def _tarih_oku(metin):
    """'YYYY-AA-GG' metnini date nesnesine çevirir."""
    return datetime.strptime(metin, TARIH_FORMATI).date()


def _kisalt(metin, uzunluk):
    """Tabloda taşmayı önlemek için uzun metinleri keser."""
    metin = str(metin)
    return metin if len(metin) <= uzunluk else metin[:uzunluk - 1] + "…"


def _tablo_yazdir(basliklar, satirlar, genislikler):
    """Verilen başlık ve satırları hizalı bir tablo olarak ekrana basar."""
    bicim = " | ".join(f"{{:<{g}}}" for g in genislikler)
    ayirac = "-+-".join("-" * g for g in genislikler)
    print(bicim.format(*basliklar))
    print(ayirac)
    for satir in satirlar:
        print(bicim.format(*[_kisalt(h, g) for h, g in zip(satir, genislikler)]))


def _temiz_metin(deger, alan_adi):
    """Boşlukları kırpar; boş metin girilmişse hata verir."""
    deger = str(deger).strip() if deger is not None else ""
    if not deger:
        raise KutuphaneHatasi(f"'{alan_adi}' boş bırakılamaz.")
    return deger


def _pozitif_tamsayi(deger, alan_adi, sifir_olabilir=False):
    """Değerin tamsayı ve pozitif (veya sıfır) olduğunu doğrular."""
    if not isinstance(deger, int) or isinstance(deger, bool):
        raise KutuphaneHatasi(f"'{alan_adi}' tam sayı olmalıdır.")
    alt_sinir = 0 if sifir_olabilir else 1
    if deger < alt_sinir:
        raise KutuphaneHatasi(f"'{alan_adi}' en az {alt_sinir} olmalıdır.")
    return deger


def _aktif_odunc_sayisi(conn, kitap_id):
    """Bir kitabın şu an ödünçte olan nüsha sayısını döndürür."""
    satir = conn.execute(
        "SELECT COUNT(*) AS adet FROM odunc_islemleri "
        "WHERE kitap_id = ? AND iade_tarihi IS NULL",
        (kitap_id,),
    ).fetchone()
    return satir["adet"]


def odunc_durumu_hesapla(son_teslim_tarihi, bugun=None):
    """
    Teslim tarihine göre kalan günü ve durum metnini hesaplar.
    Döndürür: (kalan_gun, durum_metni, geciken_mi)
    """
    bugun = bugun or date.today()
    kalan_gun = (_tarih_oku(son_teslim_tarihi) - bugun).days

    if kalan_gun > 0:
        return kalan_gun, f"{kalan_gun} gün kaldı", False
    if kalan_gun == 0:
        return 0, "Son teslim günü BUGÜN", False
    return kalan_gun, f"{abs(kalan_gun)} gün GECİKMİŞ", True


# ----------------------------------------------------------------------------
# 1) KİTAP YÖNETİMİ (CRUD)
# ----------------------------------------------------------------------------
def kitap_ekle(kitap_adi, yazar, sayfa_sayisi, kategori, toplam_stok):
    """Yeni bir kitap kaydı oluşturur ve kitabın id'sini döndürür. (CREATE)"""
    kitap_adi = _temiz_metin(kitap_adi, "Kitap adı")
    yazar = _temiz_metin(yazar, "Yazar")
    kategori = _temiz_metin(kategori, "Kategori")
    _pozitif_tamsayi(sayfa_sayisi, "Sayfa sayısı")
    _pozitif_tamsayi(toplam_stok, "Toplam stok")

    try:
        with baglanti() as conn:
            imlec = conn.execute(
                "INSERT INTO kitaplar (kitap_adi, yazar, sayfa_sayisi, kategori, toplam_stok) "
                "VALUES (?, ?, ?, ?, ?)",
                (kitap_adi, yazar, sayfa_sayisi, kategori, toplam_stok),
            )
            yeni_id = imlec.lastrowid
    except sqlite3.IntegrityError:
        raise KutuphaneHatasi(
            f"'{kitap_adi}' ({yazar}) zaten kayıtlı. Stok eklemek için kitap_guncelle() kullanın."
        )

    print(f"[+] Kitap eklendi: #{yeni_id} - {kitap_adi} ({toplam_stok} adet)")
    return yeni_id


def kitaplari_listele(yazdir=True):
    """Tüm kitapları anlık stok durumuyla listeler. (READ)"""
    with baglanti() as conn:
        satirlar = conn.execute("""
            SELECT k.id, k.kitap_adi, k.yazar, k.sayfa_sayisi, k.kategori, k.toplam_stok,
                   (SELECT COUNT(*) FROM odunc_islemleri o
                     WHERE o.kitap_id = k.id AND o.iade_tarihi IS NULL) AS oduncte
            FROM kitaplar k
            ORDER BY k.id
        """).fetchall()

    if yazdir:
        print("\n=== KİTAP LİSTESİ ===")
        if not satirlar:
            print("Kütüphanede kayıtlı kitap yok.")
        else:
            tablo = [
                (s["id"], s["kitap_adi"], s["yazar"], s["sayfa_sayisi"], s["kategori"],
                 s["toplam_stok"], s["oduncte"], s["toplam_stok"] - s["oduncte"])
                for s in satirlar
            ]
            _tablo_yazdir(
                ["ID", "Kitap Adı", "Yazar", "Sayfa", "Kategori", "Stok", "Ödünçte", "Rafta"],
                tablo,
                [3, 26, 20, 5, 10, 4, 7, 5],
            )
    return satirlar


def kitap_guncelle(kitap_id, kitap_adi=None, yazar=None, sayfa_sayisi=None,
                   kategori=None, toplam_stok=None):
    """Kitap bilgilerini günceller. (UPDATE)"""
    guncellenecekler = {}

    if kitap_adi is not None:
        guncellenecekler["kitap_adi"] = _temiz_metin(kitap_adi, "Kitap adı")
    if yazar is not None:
        guncellenecekler["yazar"] = _temiz_metin(yazar, "Yazar")
    if kategori is not None:
        guncellenecekler["kategori"] = _temiz_metin(kategori, "Kategori")
    if sayfa_sayisi is not None:
        guncellenecekler["sayfa_sayisi"] = _pozitif_tamsayi(sayfa_sayisi, "Sayfa sayısı")
    if toplam_stok is not None:
        guncellenecekler["toplam_stok"] = _pozitif_tamsayi(toplam_stok, "Toplam stok", sifir_olabilir=True)

    if not guncellenecekler:
        raise KutuphaneHatasi("Güncellenecek en az bir alan belirtmelisiniz.")

    try:
        with baglanti() as conn:
            if conn.execute("SELECT 1 FROM kitaplar WHERE id = ?", (kitap_id,)).fetchone() is None:
                raise KutuphaneHatasi(f"#{kitap_id} numaralı kitap bulunamadı.")

            if "toplam_stok" in guncellenecekler:
                oduncte = _aktif_odunc_sayisi(conn, kitap_id)
                if guncellenecekler["toplam_stok"] < oduncte:
                    raise KutuphaneHatasi(
                        f"Stok {guncellenecekler['toplam_stok']} yapılamaz: "
                        f"şu an {oduncte} adet kitap ödünçte."
                    )

            set_ifadesi = ", ".join(f"{sutun} = ?" for sutun in guncellenecekler)
            degerler = list(guncellenecekler.values()) + [kitap_id]
            conn.execute(f"UPDATE kitaplar SET {set_ifadesi} WHERE id = ?", degerler)
    except sqlite3.IntegrityError:
        raise KutuphaneHatasi("Bu kitap adı ve yazar kombinasyonu başka bir kayıtta zaten var.")

    print(f"[~] Kitap #{kitap_id} güncellendi: {', '.join(guncellenecekler)}")
    return True


def kitap_sil(kitap_id):
    """Kitabı siler. (DELETE)"""
    with baglanti() as conn:
        kitap = conn.execute(
            "SELECT kitap_adi FROM kitaplar WHERE id = ?", (kitap_id,)
        ).fetchone()
        if kitap is None:
            raise KutuphaneHatasi(f"#{kitap_id} numaralı kitap bulunamadı.")

        oduncte = _aktif_odunc_sayisi(conn, kitap_id)
        if oduncte > 0:
            raise KutuphaneHatasi(
                f"'{kitap['kitap_adi']}' silinemez: {oduncte} adedi şu an ödünçte."
            )

        conn.execute("DELETE FROM kitaplar WHERE id = ?", (kitap_id,))

    print(f"[-] Kitap silindi: #{kitap_id} - {kitap['kitap_adi']}")
    return True


# ----------------------------------------------------------------------------
# 2) ÜYE YÖNETİMİ
# ----------------------------------------------------------------------------
def uye_ekle(ad_soyad, telefon=None, eposta=None):
    """Yeni üye kaydeder ve üyenin id'sini döndürür."""
    ad_soyad = _temiz_metin(ad_soyad, "Ad soyad")
    telefon = telefon.strip() if telefon else None
    eposta = eposta.strip().lower() if eposta else None

    try:
        with baglanti() as conn:
            imlec = conn.execute(
                "INSERT INTO uyeler (ad_soyad, telefon, eposta) VALUES (?, ?, ?)",
                (ad_soyad, telefon, eposta),
            )
            yeni_id = imlec.lastrowid
    except sqlite3.IntegrityError:
        raise KutuphaneHatasi(f"'{eposta}' e-posta adresi başka bir üyeye ait.")

    print(f"[+] Üye eklendi: #{yeni_id} - {ad_soyad}")
    return yeni_id


def uyeleri_listele(yazdir=True):
    """Tüm üyeleri ve her birinin üzerindeki aktif kitap sayısını listeler."""
    with baglanti() as conn:
        satirlar = conn.execute("""
            SELECT u.id, u.ad_soyad, u.telefon, u.eposta, u.kayit_tarihi,
                   (SELECT COUNT(*) FROM odunc_islemleri o
                     WHERE o.uye_id = u.id AND o.iade_tarihi IS NULL) AS aktif_kitap
            FROM uyeler u
            ORDER BY u.id
        """).fetchall()

    if yazdir:
        print("\n=== ÜYE LİSTESİ ===")
        if not satirlar:
            print("Kayıtlı üye yok.")
        else:
            tablo = [
                (s["id"], s["ad_soyad"], s["telefon"] or "-", s["eposta"] or "-",
                 s["kayit_tarihi"], s["aktif_kitap"])
                for s in satirlar
            ]
            _tablo_yazdir(
                ["ID", "Ad Soyad", "Telefon", "E-posta", "Kayıt Tarihi", "Elindeki"],
                tablo,
                [3, 20, 14, 26, 12, 8],
            )
    return satirlar


# ----------------------------------------------------------------------------
# 3) ÖDÜNÇ ALMA VE TAKİP SİSTEMİ
# ----------------------------------------------------------------------------
def odunc_ver(kitap_id, uye_id, gun_sayisi=VARSAYILAN_ODUNC_GUN, odunc_tarihi=None):
    """Bir kitabı üyeye ödünç verir ve ödünç işleminin id'sini döndürür."""
    _pozitif_tamsayi(gun_sayisi, "Gün sayısı")
    odunc_tarihi = odunc_tarihi or date.today()
    son_teslim = odunc_tarihi + timedelta(days=gun_sayisi)

    with baglanti() as conn:
        conn.execute("BEGIN IMMEDIATE")

        kitap = conn.execute(
            "SELECT kitap_adi, toplam_stok FROM kitaplar WHERE id = ?", (kitap_id,)
        ).fetchone()
        if kitap is None:
            raise KutuphaneHatasi(f"#{kitap_id} numaralı kitap bulunamadı.")

        uye = conn.execute(
            "SELECT ad_soyad FROM uyeler WHERE id = ?", (uye_id,)
        ).fetchone()
        if uye is None:
            raise KutuphaneHatasi(f"#{uye_id} numaralı üye bulunamadı.")

        rafta = kitap["toplam_stok"] - _aktif_odunc_sayisi(conn, kitap_id)
        if rafta <= 0:
            raise KutuphaneHatasi(f"'{kitap['kitap_adi']}' için rafta boş nüsha kalmadı.")

        zaten_var = conn.execute(
            "SELECT 1 FROM odunc_islemleri "
            "WHERE kitap_id = ? AND uye_id = ? AND iade_tarihi IS NULL",
            (kitap_id, uye_id),
        ).fetchone()
        if zaten_var:
            raise KutuphaneHatasi(
                f"{uye['ad_soyad']} adlı üyede '{kitap['kitap_adi']}' zaten ödünçte."
            )

        imlec = conn.execute(
            "INSERT INTO odunc_islemleri (kitap_id, uye_id, odunc_tarihi, son_teslim_tarihi) "
            "VALUES (?, ?, ?, ?)",
            (kitap_id, uye_id, _tarih_yaz(odunc_tarihi), _tarih_yaz(son_teslim)),
        )
        odunc_id = imlec.lastrowid

    print(f"[>] Ödünç verildi (işlem #{odunc_id}): '{kitap['kitap_adi']}' -> {uye['ad_soyad']} "
          f"| Son teslim: {_tarih_yaz(son_teslim)}")
    return odunc_id


def iade_al(odunc_id, iade_tarihi=None):
    """Ödünç verilen kitabı iade alır."""
    iade_tarihi = iade_tarihi or date.today()

    with baglanti() as conn:
        islem = conn.execute("""
            SELECT o.son_teslim_tarihi, o.iade_tarihi, k.kitap_adi, u.ad_soyad
            FROM odunc_islemleri o
            JOIN kitaplar k ON k.id = o.kitap_id
            JOIN uyeler   u ON u.id = o.uye_id
            WHERE o.id = ?
        """, (odunc_id,)).fetchone()

        if islem is None:
            raise KutuphaneHatasi(f"#{odunc_id} numaralı ödünç işlemi bulunamadı.")
        if islem["iade_tarihi"] is not None:
            raise KutuphaneHatasi(f"#{odunc_id} numaralı işlem zaten {islem['iade_tarihi']} tarihinde iade edilmiş.")

        conn.execute(
            "UPDATE odunc_islemleri SET iade_tarihi = ? WHERE id = ?",
            (_tarih_yaz(iade_tarihi), odunc_id),
        )

    gecikme = max(0, (iade_tarihi - _tarih_oku(islem["son_teslim_tarihi"])).days)
    mesaj = "zamanında" if gecikme == 0 else f"{gecikme} gün GECİKMELİ"
    print(f"[<] İade alındı (işlem #{odunc_id}): '{islem['kitap_adi']}' <- {islem['ad_soyad']} ({mesaj})")
    return gecikme


def odunc_durumu_kontrol(odunc_id, bugun=None):
    """Tek bir ödünç işleminin süre durumunu kontrol eder."""
    with baglanti() as conn:
        islem = conn.execute("""
            SELECT o.id, k.kitap_adi, u.ad_soyad, o.odunc_tarihi,
                   o.son_teslim_tarihi, o.iade_tarihi
            FROM odunc_islemleri o
            JOIN kitaplar k ON k.id = o.kitap_id
            JOIN uyeler   u ON u.id = o.uye_id
            WHERE o.id = ?
        """, (odunc_id,)).fetchone()

    if islem is None:
        raise KutuphaneHatasi(f"#{odunc_id} numaralı ödünç işlemi bulunamadı.")

    if islem["iade_tarihi"] is not None:
        kalan_gun, durum, geciken = None, f"İade edildi ({islem['iade_tarihi']})", False
    else:
        kalan_gun, durum, geciken = odunc_durumu_hesapla(islem["son_teslim_tarihi"], bugun)

    sonuc = {
        "odunc_id": islem["id"], "kitap_adi": islem["kitap_adi"], "uye": islem["ad_soyad"],
        "odunc_tarihi": islem["odunc_tarihi"], "son_teslim_tarihi": islem["son_teslim_tarihi"],
        "kalan_gun": kalan_gun, "durum": durum, "geciken_mi": geciken,
    }
    isaret = "!!" if geciken else "OK"
    print(f"[{isaret}] İşlem #{odunc_id}: '{sonuc['kitap_adi']}' / {sonuc['uye']} -> {durum}")
    return sonuc


def aktif_oduncleri_listele(sadece_geciken=False, bugun=None, yazdir=True):
    """Henüz iade edilmemiş tüm ödünç işlemlerini süre durumuyla listeler."""
    with baglanti() as conn:
        satirlar = conn.execute("""
            SELECT o.id, k.kitap_adi, u.ad_soyad, o.odunc_tarihi, o.son_teslim_tarihi
            FROM odunc_islemleri o
            JOIN kitaplar k ON k.id = o.kitap_id
            JOIN uyeler   u ON u.id = o.uye_id
            WHERE o.iade_tarihi IS NULL
            ORDER BY o.son_teslim_tarihi ASC
        """).fetchall()

    liste = []
    for s in satirlar:
        kalan, durum, geciken = odunc_durumu_hesapla(s["son_teslim_tarihi"], bugun)
        if sadece_geciken and not geciken:
            continue
        liste.append({
            "odunc_id": s["id"],
            "kitap_adi": s["kitap_adi"],
            "uye": s["ad_soyad"],
            "odunc_tarihi": s["odunc_tarihi"],
            "son_teslim_tarihi": s["son_teslim_tarihi"],
            "kalan_gun": kalan,
            "durum": durum,
            "geciken_mi": geciken,
        })

    if yazdir:
        baslik = "\n=== GECİKEN ÖDÜNÇLER ===" if sadece_geciken else "\n=== AKTİF ÖDÜNÇ LİSTESİ ==="
        print(baslik)
        if not liste:
            print("Kayıt bulunamadı.")
        else:
            tablo = [
                (k["odunc_id"], k["kitap_adi"], k["uye"],
                 k["odunc_tarihi"], k["son_teslim_tarihi"], k["durum"])
                for k in liste
            ]
            _tablo_yazdir(
                ["ID", "Kitap Adı", "Üye", "Ödünç T.", "Son Teslim", "Durum"],
                tablo,
                [3, 24, 18, 10, 10, 22],
            )
    return liste


# ----------------------------------------------------------------------------
# 4) RAPORLAMA VE ÖRNEK VERİ YÜKLEME
# ----------------------------------------------------------------------------
def kutuphane_durumu_goster(bugun=None, yazdir=True):
    """Kütüphanenin genel istatistiklerini ve özet durumunu hesaplar."""
    with baglanti() as conn:
        kitap_ozet = conn.execute("""
            SELECT COUNT(*) AS cesit, COALESCE(SUM(toplam_stok), 0) AS toplam_stok
            FROM kitaplar
        """).fetchone()
        uye_sayisi = conn.execute("SELECT COUNT(*) AS adet FROM uyeler").fetchone()["adet"]
        aktif_odunc = conn.execute(
            "SELECT COUNT(*) AS adet FROM odunc_islemleri WHERE iade_tarihi IS NULL"
        ).fetchone()["adet"]

    geciken_sayisi = len(aktif_oduncleri_listele(sadece_geciken=True, bugun=bugun, yazdir=False))
    toplam_stok = kitap_ozet["toplam_stok"]
    rafta_kalan = toplam_stok - aktif_odunc

    ozet = {
        "kitap_cesidi": kitap_ozet["cesit"],
        "toplam_stok": toplam_stok,
        "oduncte": aktif_odunc,
        "rafta": rafta_kalan,
        "geciken": geciken_sayisi,
        "uye_sayisi": uye_sayisi,
    }

    if yazdir:
        print("\n=== KÜTÜPHANE GENEL DURUM RAPORU ===")
        print(f"  Toplam Kitap Başlığı (Çeşit) : {ozet['kitap_cesidi']}")
        print(f"  Toplam Kitap Nüshası (Stok)  : {ozet['toplam_stok']}")
        print(f"  Şu An Ödünçte Olan           : {ozet['oduncte']}")
        print(f"  Rafta Mevcut Olan            : {ozet['rafta']}")
        print(f"  Teslim Tarihi Geçmiş (Geciken): {ozet['geciken']}")
        print(f"  Kayıtlı Toplam Üye           : {ozet['uye_sayisi']}")
    return ozet


def ornek_verileri_yukle():
    """Sistemi hızlıca test etmek için örnek kitap, üye ve ödünç kayıtları ekler."""
    ornek_kitaplar = [
        ("Suç ve Ceza", "Fyodor Dostoyevski", 687, "Klasik", 4),
        ("Saatleri Ayarlama Enstitüsü", "Ahmet Hamdi Tanpınar", 382, "Türk Edebiyatı", 3),
        ("1984", "George Orwell", 352, "Distopya", 5),
        ("Kürk Mantolu Madonna", "Sabahattin Ali", 160, "Türk Edebiyatı", 4),
        ("Sefiller", "Victor Hugo", 1463, "Klasik", 2),
        ("Simyacı", "Paulo Coelho", 188, "Roman", 3),
    ]
    ornek_uyeler = [
        ("Ahmet Yılmaz", "0532 111 22 33", "ahmet.yilmaz@ornek.com"),
        ("Zeynep Kaya", "0544 222 33 44", "zeynep.kaya@ornek.com"),
        ("Mehmet Demir", "0555 333 44 55", "mehmet.demir@ornek.com"),
        ("Elif Çelik", "0533 444 55 66", "elif.celik@ornek.com"),
    ]

    eklenen_kitap = 0
    for ad, yazar, sayfa, kat, stok in ornek_kitaplar:
        try:
            kitap_ekle(ad, yazar, sayfa, kat, stok)
            eklenen_kitap += 1
        except KutuphaneHatasi:
            pass

    eklenen_uye = 0
    for ad_soyad, tel, eposta in ornek_uyeler:
        try:
            uye_ekle(ad_soyad, tel, eposta)
            eklenen_uye += 1
        except KutuphaneHatasi:
            pass

    # Örnek ödünç işlemleri (biri gecikmiş, biri normal, biri bugün teslim)
    bugun = date.today()
    try:
        odunc_ver(1, 1, gun_sayisi=14, odunc_tarihi=bugun - timedelta(days=20))  # 6 gün gecikmiş
    except KutuphaneHatasi:
        pass
    try:
        odunc_ver(2, 2, gun_sayisi=14, odunc_tarihi=bugun - timedelta(days=5))   # 9 gün kaldı
    except KutuphaneHatasi:
        pass
    try:
        odunc_ver(3, 3, gun_sayisi=14, odunc_tarihi=bugun - timedelta(days=14))  # Son teslim bugün
    except KutuphaneHatasi:
        pass

    print(f"[*] Örnek veri yükleme tamamlandı ({eklenen_kitap} yeni kitap, {eklenen_uye} yeni üye).")


# ----------------------------------------------------------------------------
# 5) TERMİNAL (CLI) KULLANICI MENÜSÜ
# ----------------------------------------------------------------------------
def _sayi_iste(mesaj, bos_gecilebilir=False):
    """Kullanıcıdan güvenli bir şekilde tam sayı alır."""
    deger = input(mesaj).strip()
    if bos_gecilebilir and deger == "":
        return None
    try:
        return int(deger)
    except ValueError:
        raise KutuphaneHatasi("Lütfen geçerli bir tam sayı girin.")


def ana_menu():
    """Etkileşimli komut satırı (CLI) menüsünü çalıştırır."""
    veritabani_olustur()

    menu_metni = """
========================================================
     KÜTÜPHANE YÖNETİM VE ÖDÜNÇ TAKİP SİSTEMİ (CLI)
========================================================
  [1] Kitapları Listele          [7]  Kitap Ödünç Ver
  [2] Yeni Kitap Ekle            [8]  Kitap İade Al
  [3] Kitap Bilgisi/Stok Güncelle[9]  Aktif Ödünçleri Listele
  [4] Kitap Sil                  [10] Sadece Gecikenleri Listele
  [5] Üyeleri Listele            [11] Kütüphane Genel Durum Raporu
  [6] Yeni Üye Ekle              [12] Örnek Verileri Yükle
  [0] Çıkış
========================================================"""

    while True:
        print(menu_metni)
        secim = input("Seçiminiz (0-12): ").strip()

        try:
            if secim == "1":
                kitaplari_listele()

            elif secim == "2":
                print("\n--- YENİ KİTAP EKLE ---")
                ad = input("Kitap Adı    : ")
                yazar = input("Yazar        : ")
                sayfa = _sayi_iste("Sayfa Sayısı : ")
                kategori = input("Kategori     : ")
                stok = _sayi_iste("Toplam Stok  : ")
                kitap_ekle(ad, yazar, sayfa, kategori, stok)

            elif secim == "3":
                print("\n--- KİTAP GÜNCELLE (Değiştirmek istemediğiniz alanı boş bırakın) ---")
                kitaplari_listele()
                kid = _sayi_iste("\nGüncellenecek Kitap ID: ")
                ad = input("Yeni Kitap Adı    : ").strip() or None
                yazar = input("Yeni Yazar        : ").strip() or None
                sayfa = _sayi_iste("Yeni Sayfa Sayısı : ", bos_gecilebilir=True)
                kategori = input("Yeni Kategori     : ").strip() or None
                stok = _sayi_iste("Yeni Toplam Stok  : ", bos_gecilebilir=True)
                kitap_guncelle(kid, kitap_adi=ad, yazar=yazar, sayfa_sayisi=sayfa,
                               kategori=kategori, toplam_stok=stok)

            elif secim == "4":
                print("\n--- KİTAP SİL ---")
                kitaplari_listele()
                kid = _sayi_iste("\nSilinecek Kitap ID: ")
                kitap_sil(kid)

            elif secim == "5":
                uyeleri_listele()

            elif secim == "6":
                print("\n--- YENİ ÜYE EKLE ---")
                ad_soyad = input("Ad Soyad               : ")
                telefon = input("Telefon (isteğe bağlı) : ")
                eposta = input("E-posta (isteğe bağlı) : ")
                uye_ekle(ad_soyad, telefon, eposta)

            elif secim == "7":
                print("\n--- KİTAP ÖDÜNÇ VER ---")
                kitaplari_listele()
                uyeleri_listele()
                kid = _sayi_iste("\nÖdünç Verilecek Kitap ID : ")
                uid = _sayi_iste("Ödünç Alacak Üye ID      : ")
                gun = _sayi_iste(f"Ödünç Süresi (Varsayılan {VARSAYILAN_ODUNC_GUN} gün, boş geçilebilir): ",
                                 bos_gecilebilir=True)
                odunc_ver(kid, uid, gun_sayisi=gun or VARSAYILAN_ODUNC_GUN)

            elif secim == "8":
                print("\n--- KİTAP İADE AL ---")
                aktif_oduncleri_listele()
                oid = _sayi_iste("\nİade Alınacak Ödünç İşlem ID: ")
                iade_al(oid)

            elif secim == "9":
                aktif_oduncleri_listele(sadece_geciken=False)

            elif secim == "10":
                aktif_oduncleri_listele(sadece_geciken=True)

            elif secim == "11":
                kutuphane_durumu_goster()

            elif secim == "12":
                ornek_verileri_yukle()

            elif secim == "0":
                print("Sistemden çıkılıyor. İyi çalışmalar!")
                break

            else:
                print("[!] Geçersiz seçim. Lütfen 0 ile 12 arasında bir değer girin.")

        except KutuphaneHatasi as hata:
            print(f"\n[HATA] {hata}")
        except KeyboardInterrupt:
            print("\nİşlem kullanıcı tarafından iptal edildi. Çıkılıyor...")
            break


if __name__ == "__main__":
    ana_menu()