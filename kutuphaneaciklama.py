#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import hashlib
import hmac
import json
import os
import sqlite3
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from contextlib import contextmanager
from datetime import date, datetime, timedelta

DB_ADI = "kutuphane.db"
VARSAYILAN_ODUNC_GUN = 14
TARIH_FORMATI = "%Y-%m-%d"
PBKDF2_TUR = 200_000
EN_KISA_SIFRE = 6

GOOGLE_URL = "https://www.googleapis.com/books/v1/volumes"
OPENLIBRARY_URL = "https://openlibrary.org/search.json"
ISTEK_ZAMAN_ASIMI = 8
ISTEMCI_ADI = "KurumsalKutuphane/1.0 (kitap arama)"
ONBELLEK_SURESI = 3600
GOOGLE_BEKLEME_SURESI = 120
ARAMA_TURLERI = ("otomatik", "kitap", "yazar", "isbn")

_onbellek = {}
_onbellek_kilidi = threading.Lock()
_google_bekleme = {"bitis": 0.0}


class KutuphaneHatasi(Exception):
    pass


@contextmanager
def baglanti():
    conn = sqlite3.connect(DB_ADI, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _sifre_hashle(sifre):
    tuz = os.urandom(16)
    ozet = hashlib.pbkdf2_hmac("sha256", str(sifre).encode("utf-8"), tuz, PBKDF2_TUR)
    return f"pbkdf2${PBKDF2_TUR}${tuz.hex()}${ozet.hex()}"


def _sifre_dogru_mu(sifre, kayitli_hash):
    sifre_bayt = str(sifre).encode("utf-8")
    if kayitli_hash.startswith("pbkdf2$"):
        try:
            _, tur, tuz, ozet = kayitli_hash.split("$")
            hesap = hashlib.pbkdf2_hmac("sha256", sifre_bayt, bytes.fromhex(tuz), int(tur)).hex()
        except ValueError:
            return False
        return hmac.compare_digest(hesap, ozet)
    return hmac.compare_digest(hashlib.sha256(sifre_bayt).hexdigest(), kayitli_hash)


def veritabani_olustur():
    with baglanti() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS kitaplar (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                kitap_adi      TEXT    NOT NULL COLLATE NOCASE,
                yazar          TEXT    NOT NULL COLLATE NOCASE,
                yayinevi       TEXT    DEFAULT 'Belirtilmemiş' COLLATE NOCASE,
                sayfa_sayisi   INTEGER NOT NULL CHECK (sayfa_sayisi > 0),
                kategori       TEXT    NOT NULL COLLATE NOCASE,
                isbn           TEXT,
                kapak_url      TEXT,
                toplam_stok    INTEGER NOT NULL CHECK (toplam_stok >= 0),
                eklenme_tarihi TEXT    NOT NULL DEFAULT (date('now', 'localtime')),
                UNIQUE (kitap_adi, yazar)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS uyeler (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                ad_soyad     TEXT NOT NULL COLLATE NOCASE,
                telefon      TEXT,
                eposta       TEXT NOT NULL UNIQUE COLLATE NOCASE,
                sifre_hash   TEXT NOT NULL,
                rol          TEXT NOT NULL DEFAULT 'uye' CHECK (rol IN ('admin', 'uye')),
                kayit_tarihi TEXT NOT NULL DEFAULT (date('now', 'localtime'))
            )
        """)
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
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_odunc_aktif
            ON odunc_islemleri (kitap_id, iade_tarihi)
        """)

        if not conn.execute("SELECT 1 FROM uyeler WHERE rol = 'admin' LIMIT 1").fetchone():
            conn.execute(
                "INSERT INTO uyeler (ad_soyad, telefon, eposta, sifre_hash, rol) VALUES (?, ?, ?, ?, ?)",
                ("Sistem Yöneticisi", "0500 000 00 00", "admin@kutuphane.com", _sifre_hashle("admin123"), "admin"),
            )


def _tarih_yaz(tarih):
    return tarih.strftime(TARIH_FORMATI)


def _tarih_oku(metin):
    return datetime.strptime(metin, TARIH_FORMATI).date()


def _kisalt(metin, uzunluk):
    metin = str(metin)
    return metin if len(metin) <= uzunluk else metin[:uzunluk - 1] + "…"


def _tablo_yazdir(basliklar, satirlar, genislikler):
    bicim = " | ".join(f"{{:<{g}}}" for g in genislikler)
    print(bicim.format(*basliklar))
    print("-+-".join("-" * g for g in genislikler))
    for satir in satirlar:
        print(bicim.format(*[_kisalt(h, g) for h, g in zip(satir, genislikler)]))


def _temiz_metin(deger, alan_adi):
    deger = str(deger).strip() if deger is not None else ""
    if not deger:
        raise KutuphaneHatasi(f"'{alan_adi}' boş bırakılamaz.")
    return deger


def _pozitif_tamsayi(deger, alan_adi, sifir_olabilir=False):
    if not isinstance(deger, int) or isinstance(deger, bool):
        raise KutuphaneHatasi(f"'{alan_adi}' tam sayı olmalıdır.")
    alt_sinir = 0 if sifir_olabilir else 1
    if deger < alt_sinir:
        raise KutuphaneHatasi(f"'{alan_adi}' en az {alt_sinir} olmalıdır.")
    return deger


def _isbn_temizle(isbn):
    return "".join(c for c in str(isbn or "") if c.isalnum()).upper()


def _isbn_benzeri_mi(metin):
    temiz = _isbn_temizle(metin)
    return len(temiz) in (10, 13) and temiz[:-1].isdigit() and (temiz[-1].isdigit() or temiz[-1] == "X")


def _eposta_gecerli_mi(eposta):
    yerel, ayrac, alan = eposta.partition("@")
    return bool(yerel) and bool(ayrac) and "." in alan and " " not in eposta


def _aktif_odunc_sayisi(conn, kitap_id):
    return conn.execute(
        "SELECT COUNT(*) AS adet FROM odunc_islemleri WHERE kitap_id = ? AND iade_tarihi IS NULL",
        (kitap_id,),
    ).fetchone()["adet"]


def odunc_durumu_hesapla(son_teslim_tarihi, bugun=None):
    bugun = bugun or date.today()
    kalan_gun = (_tarih_oku(son_teslim_tarihi) - bugun).days
    if kalan_gun > 0:
        return kalan_gun, f"{kalan_gun} gün kaldı", False
    if kalan_gun == 0:
        return 0, "Son teslim günü BUGÜN", False
    return kalan_gun, f"{abs(kalan_gun)} gün GECİKMİŞ", True


def _json_getir(url):
    istek = urllib.request.Request(url, headers={"User-Agent": ISTEMCI_ADI, "Accept": "application/json"})
    with urllib.request.urlopen(istek, timeout=ISTEK_ZAMAN_ASIMI) as yanit:
        return json.loads(yanit.read().decode("utf-8"))


def _bekleme_suresi(hata):
    try:
        return min(float(hata.headers.get("Retry-After", "")), 3.0)
    except (TypeError, ValueError, AttributeError):
        return 1.5


def _google_ara(metin, tur, adet, api_anahtari):
    if time.time() < _google_bekleme["bitis"]:
        raise KutuphaneHatasi("Google Books istek sınırı nedeniyle kısa süreliğine devre dışı.")

    sorgu = {
        "otomatik": metin,
        "kitap": f"intitle:{metin}",
        "yazar": f"inauthor:{metin}",
        "isbn": f"isbn:{metin}",
    }[tur]
    parametreler = {"q": sorgu, "maxResults": adet, "printType": "books", "orderBy": "relevance"}
    if api_anahtari:
        parametreler["key"] = api_anahtari
    url = f"{GOOGLE_URL}?{urllib.parse.urlencode(parametreler)}"

    veri = None
    for deneme in range(2):
        try:
            veri = _json_getir(url)
            break
        except urllib.error.HTTPError as hata:
            if hata.code in (429, 500, 502, 503) and deneme == 0:
                time.sleep(_bekleme_suresi(hata))
                continue
            if hata.code == 429:
                _google_bekleme["bitis"] = time.time() + GOOGLE_BEKLEME_SURESI
            raise KutuphaneHatasi(f"Google Books yanıt vermedi (HTTP {hata.code}).")
        except (urllib.error.URLError, OSError, ValueError) as hata:
            raise KutuphaneHatasi(f"Google Books bağlantı hatası: {hata}.")

    sonuclar = []
    for oge in (veri or {}).get("items", []):
        bilgi = oge.get("volumeInfo", {})
        baslik = (bilgi.get("title") or "").strip()
        if not baslik:
            continue

        kodlar = {k.get("type"): k.get("identifier") for k in bilgi.get("industryIdentifiers", [])}
        gorseller = bilgi.get("imageLinks", {})
        kapak = gorseller.get("thumbnail") or gorseller.get("smallThumbnail")
        yil = (bilgi.get("publishedDate") or "")[:4]

        sonuclar.append({
            "kitap_adi": baslik,
            "yazar": ", ".join(y.strip() for y in bilgi.get("authors") or [] if y.strip()) or "Bilinmeyen Yazar",
            "yayinevi": (bilgi.get("publisher") or "").strip() or "Belirtilmemiş",
            "yil": yil if yil.isdigit() else "",
            "sayfa_sayisi": bilgi.get("pageCount") or None,
            "kategori": ", ".join(bilgi.get("categories") or []) or "Genel",
            "isbn": kodlar.get("ISBN_13") or kodlar.get("ISBN_10"),
            "kapak_url": kapak.replace("http://", "https://") if kapak else None,
            "kaynak": "Google Books",
        })
    return sonuclar


def _openlibrary_ara(metin, tur, adet):
    alan = {"otomatik": "q", "kitap": "title", "yazar": "author", "isbn": "isbn"}[tur]
    parametreler = {
        alan: metin,
        "limit": adet,
        "fields": "title,author_name,publisher,first_publish_year,number_of_pages_median,isbn,cover_i,subject",
    }
    url = f"{OPENLIBRARY_URL}?{urllib.parse.urlencode(parametreler)}"

    try:
        veri = _json_getir(url)
    except urllib.error.HTTPError as hata:
        raise KutuphaneHatasi(f"Open Library yanıt vermedi (HTTP {hata.code}).")
    except (urllib.error.URLError, OSError, ValueError) as hata:
        raise KutuphaneHatasi(f"Open Library bağlantı hatası: {hata}.")

    sonuclar = []
    for doc in veri.get("docs", []):
        baslik = (doc.get("title") or "").strip()
        if not baslik:
            continue

        isbnler = doc.get("isbn") or []
        isbn = next((i for i in isbnler if len(i) == 13), None) or (isbnler[0] if isbnler else None)
        yayinevleri = doc.get("publisher") or []
        konular = doc.get("subject") or []

        sonuclar.append({
            "kitap_adi": baslik,
            "yazar": ", ".join(y.strip() for y in doc.get("author_name") or [] if y.strip()) or "Bilinmeyen Yazar",
            "yayinevi": yayinevleri[0] if yayinevleri else "Belirtilmemiş",
            "yil": str(doc.get("first_publish_year") or ""),
            "sayfa_sayisi": doc.get("number_of_pages_median") or None,
            "kategori": konular[0] if konular else "Genel",
            "isbn": isbn,
            "kapak_url": f"https://covers.openlibrary.org/b/id/{doc['cover_i']}-M.jpg" if doc.get("cover_i") else None,
            "kaynak": "Open Library",
        })
    return sonuclar


def _tekrarlari_ele(sonuclar):
    gorulen, temiz = set(), []
    for s in sonuclar:
        anahtar = (s["kitap_adi"].casefold(), s["yazar"].casefold(), s["yayinevi"].casefold(), s["yil"])
        if anahtar not in gorulen:
            gorulen.add(anahtar)
            temiz.append(s)
    return temiz


def kitap_ara(arama_metni, tur="otomatik", maksimum_sonuc=10, api_anahtari=None):
    metin = _temiz_metin(arama_metni, "Arama metni")
    if tur not in ARAMA_TURLERI:
        raise KutuphaneHatasi("Geçersiz arama türü.")
    if tur == "otomatik" and _isbn_benzeri_mi(metin):
        tur = "isbn"
    if tur == "isbn":
        metin = _isbn_temizle(metin)

    anahtar = api_anahtari or os.environ.get("GOOGLE_BOOKS_API_KEY")
    onbellek_anahtari = (tur, metin.casefold(), maksimum_sonuc)

    with _onbellek_kilidi:
        kayit = _onbellek.get(onbellek_anahtari)
    if kayit and time.time() - kayit[0] < ONBELLEK_SURESI:
        return kayit[1], None

    google_hatasi = None
    sonuclar = []
    try:
        sonuclar = _tekrarlari_ele(_google_ara(metin, tur, maksimum_sonuc, anahtar))
    except KutuphaneHatasi as hata:
        google_hatasi = str(hata)

    if not sonuclar:
        try:
            sonuclar = _tekrarlari_ele(_openlibrary_ara(metin, tur, maksimum_sonuc))
        except KutuphaneHatasi as hata:
            if google_hatasi:
                raise KutuphaneHatasi(f"Arama servislerine ulaşılamadı. {google_hatasi} {hata}")
        else:
            if google_hatasi and sonuclar:
                return sonuclar, f"{google_hatasi} Sonuçlar Open Library kaynağından getirildi."

    if sonuclar and not google_hatasi:
        with _onbellek_kilidi:
            _onbellek[onbellek_anahtari] = (time.time(), sonuclar)
    return sonuclar, google_hatasi


def kitap_ekle(kitap_adi, yazar, sayfa_sayisi, kategori, toplam_stok,
               yayinevi="Belirtilmemiş", isbn=None, kapak_url=None):
    kitap_adi = _temiz_metin(kitap_adi, "Kitap adı")
    yazar = _temiz_metin(yazar, "Yazar")
    kategori = _temiz_metin(kategori, "Kategori")
    yayinevi = yayinevi.strip() if yayinevi and yayinevi.strip() else "Belirtilmemiş"
    isbn = _isbn_temizle(isbn) or None
    kapak_url = kapak_url.strip() if kapak_url and kapak_url.strip() else None
    _pozitif_tamsayi(sayfa_sayisi, "Sayfa sayısı")
    _pozitif_tamsayi(toplam_stok, "Toplam stok")

    try:
        with baglanti() as conn:
            if isbn and conn.execute("SELECT 1 FROM kitaplar WHERE isbn = ?", (isbn,)).fetchone():
                raise KutuphaneHatasi(f"{isbn} ISBN numaralı kitap zaten kayıtlı.")
            imlec = conn.execute(
                "INSERT INTO kitaplar (kitap_adi, yazar, yayinevi, sayfa_sayisi, kategori, isbn, kapak_url, toplam_stok) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (kitap_adi, yazar, yayinevi, sayfa_sayisi, kategori, isbn, kapak_url, toplam_stok),
            )
            yeni_id = imlec.lastrowid
    except sqlite3.IntegrityError:
        raise KutuphaneHatasi(
            f"'{kitap_adi}' ({yazar}) zaten kayıtlı. Stok eklemek için kitap listesinden stok bilgisini güncelleyin."
        )

    print(f"[+] Kitap eklendi: #{yeni_id} {kitap_adi} ({toplam_stok} adet)")
    return yeni_id


def kitaplari_listele(yazdir=True):
    with baglanti() as conn:
        satirlar = conn.execute("""
            SELECT k.id, k.kitap_adi, k.yazar, k.yayinevi, k.sayfa_sayisi, k.kategori,
                   k.isbn, k.kapak_url, k.toplam_stok,
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
                (s["id"], s["kitap_adi"], s["yazar"], s["yayinevi"], s["sayfa_sayisi"],
                 s["kategori"], s["toplam_stok"], s["oduncte"], s["toplam_stok"] - s["oduncte"])
                for s in satirlar
            ]
            _tablo_yazdir(
                ["ID", "Kitap Adı", "Yazar", "Yayınevi", "Sayfa", "Kategori", "Stok", "Ödünçte", "Rafta"],
                tablo,
                [3, 22, 18, 15, 5, 10, 4, 7, 5],
            )
    return satirlar


def kitap_guncelle(kitap_id, kitap_adi=None, yazar=None, sayfa_sayisi=None,
                   kategori=None, toplam_stok=None, yayinevi=None):
    guncellenecekler = {}

    if kitap_adi is not None:
        guncellenecekler["kitap_adi"] = _temiz_metin(kitap_adi, "Kitap adı")
    if yazar is not None:
        guncellenecekler["yazar"] = _temiz_metin(yazar, "Yazar")
    if yayinevi is not None:
        guncellenecekler["yayinevi"] = _temiz_metin(yayinevi, "Yayınevi")
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
                        f"Stok {guncellenecekler['toplam_stok']} yapılamaz: şu an {oduncte} adet kitap ödünçte."
                    )

            set_ifadesi = ", ".join(f"{sutun} = ?" for sutun in guncellenecekler)
            conn.execute(
                f"UPDATE kitaplar SET {set_ifadesi} WHERE id = ?",
                list(guncellenecekler.values()) + [kitap_id],
            )
    except sqlite3.IntegrityError:
        raise KutuphaneHatasi("Bu kitap adı ve yazar kombinasyonu başka bir kayıtta zaten var.")

    print(f"[~] Kitap #{kitap_id} güncellendi: {', '.join(guncellenecekler)}")
    return True


def kitap_sil(kitap_id):
    with baglanti() as conn:
        kitap = conn.execute("SELECT kitap_adi FROM kitaplar WHERE id = ?", (kitap_id,)).fetchone()
        if kitap is None:
            raise KutuphaneHatasi(f"#{kitap_id} numaralı kitap bulunamadı.")

        oduncte = _aktif_odunc_sayisi(conn, kitap_id)
        if oduncte > 0:
            raise KutuphaneHatasi(f"'{kitap['kitap_adi']}' silinemez: {oduncte} adedi şu an ödünçte.")

        conn.execute("DELETE FROM kitaplar WHERE id = ?", (kitap_id,))

    print(f"[-] Kitap silindi: #{kitap_id} {kitap['kitap_adi']}")
    return True


def kullanici_dogrula(eposta, sifre):
    eposta = _temiz_metin(eposta, "E-posta").lower()
    sifre = _temiz_metin(sifre, "Şifre")

    with baglanti() as conn:
        kullanici = conn.execute(
            "SELECT id, ad_soyad, telefon, eposta, rol, kayit_tarihi, sifre_hash FROM uyeler WHERE eposta = ?",
            (eposta,),
        ).fetchone()

        if kullanici is None or not _sifre_dogru_mu(sifre, kullanici["sifre_hash"]):
            raise KutuphaneHatasi("Hatalı e-posta adresi veya şifre!")

        if not kullanici["sifre_hash"].startswith("pbkdf2$"):
            conn.execute(
                "UPDATE uyeler SET sifre_hash = ? WHERE id = ?",
                (_sifre_hashle(sifre), kullanici["id"]),
            )

    bilgi = dict(kullanici)
    del bilgi["sifre_hash"]
    return bilgi


def sifre_degistir(uye_id, eski_sifre, yeni_sifre):
    eski_sifre = _temiz_metin(eski_sifre, "Mevcut şifre")
    yeni_sifre = _temiz_metin(yeni_sifre, "Yeni şifre")
    if len(yeni_sifre) < EN_KISA_SIFRE:
        raise KutuphaneHatasi(f"Yeni şifre en az {EN_KISA_SIFRE} karakter olmalıdır.")

    with baglanti() as conn:
        satir = conn.execute("SELECT sifre_hash FROM uyeler WHERE id = ?", (uye_id,)).fetchone()
        if satir is None:
            raise KutuphaneHatasi(f"#{uye_id} numaralı üye bulunamadı.")
        if not _sifre_dogru_mu(eski_sifre, satir["sifre_hash"]):
            raise KutuphaneHatasi("Mevcut şifre hatalı.")
        conn.execute("UPDATE uyeler SET sifre_hash = ? WHERE id = ?", (_sifre_hashle(yeni_sifre), uye_id))
    return True


def uye_ekle(ad_soyad, telefon=None, eposta=None, sifre="123456", rol="uye"):
    ad_soyad = _temiz_metin(ad_soyad, "Ad soyad")
    eposta = _temiz_metin(eposta, "E-posta").lower()
    sifre = _temiz_metin(sifre, "Şifre")
    telefon = telefon.strip() if telefon else None

    if not _eposta_gecerli_mi(eposta):
        raise KutuphaneHatasi("Geçerli bir e-posta adresi girin.")
    if len(sifre) < EN_KISA_SIFRE:
        raise KutuphaneHatasi(f"Şifre en az {EN_KISA_SIFRE} karakter olmalıdır.")
    if rol not in ("admin", "uye"):
        raise KutuphaneHatasi("Geçersiz rol! Rol 'admin' veya 'uye' olmalıdır.")

    try:
        with baglanti() as conn:
            imlec = conn.execute(
                "INSERT INTO uyeler (ad_soyad, telefon, eposta, sifre_hash, rol) VALUES (?, ?, ?, ?, ?)",
                (ad_soyad, telefon, eposta, _sifre_hashle(sifre), rol),
            )
            yeni_id = imlec.lastrowid
    except sqlite3.IntegrityError:
        raise KutuphaneHatasi(f"'{eposta}' e-posta adresi başka bir üyeye ait.")

    print(f"[+] Üye eklendi: #{yeni_id} {ad_soyad} ({rol})")
    return yeni_id


def uyeleri_listele(yazdir=True):
    with baglanti() as conn:
        satirlar = conn.execute("""
            SELECT u.id, u.ad_soyad, u.telefon, u.eposta, u.rol, u.kayit_tarihi,
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
                (s["id"], s["ad_soyad"], s["rol"], s["telefon"] or "-", s["eposta"] or "-",
                 s["kayit_tarihi"], s["aktif_kitap"])
                for s in satirlar
            ]
            _tablo_yazdir(
                ["ID", "Ad Soyad", "Rol", "Telefon", "E-posta", "Kayıt Tarihi", "Elindeki"],
                tablo,
                [3, 18, 6, 14, 24, 12, 8],
            )
    return satirlar


def odunc_ver(kitap_id, uye_id, gun_sayisi=VARSAYILAN_ODUNC_GUN, odunc_tarihi=None):
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

        uye = conn.execute("SELECT ad_soyad FROM uyeler WHERE id = ?", (uye_id,)).fetchone()
        if uye is None:
            raise KutuphaneHatasi(f"#{uye_id} numaralı üye bulunamadı.")

        if kitap["toplam_stok"] - _aktif_odunc_sayisi(conn, kitap_id) <= 0:
            raise KutuphaneHatasi(f"'{kitap['kitap_adi']}' için rafta boş nüsha kalmadı.")

        zaten_var = conn.execute(
            "SELECT 1 FROM odunc_islemleri WHERE kitap_id = ? AND uye_id = ? AND iade_tarihi IS NULL",
            (kitap_id, uye_id),
        ).fetchone()
        if zaten_var:
            raise KutuphaneHatasi(f"{uye['ad_soyad']} adlı üyede '{kitap['kitap_adi']}' zaten ödünçte.")

        imlec = conn.execute(
            "INSERT INTO odunc_islemleri (kitap_id, uye_id, odunc_tarihi, son_teslim_tarihi) VALUES (?, ?, ?, ?)",
            (kitap_id, uye_id, _tarih_yaz(odunc_tarihi), _tarih_yaz(son_teslim)),
        )
        odunc_id = imlec.lastrowid

    print(f"[>] Ödünç verildi (işlem #{odunc_id}): '{kitap['kitap_adi']}' -> {uye['ad_soyad']} "
          f"| Son teslim: {_tarih_yaz(son_teslim)}")
    return odunc_id


def iade_al(odunc_id, iade_tarihi=None):
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

        imlec = conn.execute(
            "UPDATE odunc_islemleri SET iade_tarihi = ? WHERE id = ? AND iade_tarihi IS NULL",
            (_tarih_yaz(iade_tarihi), odunc_id),
        )
        if imlec.rowcount == 0:
            raise KutuphaneHatasi(f"#{odunc_id} numaralı işlem zaten {islem['iade_tarihi']} tarihinde iade edilmiş.")

    gecikme = max(0, (iade_tarihi - _tarih_oku(islem["son_teslim_tarihi"])).days)
    mesaj = "zamanında" if gecikme == 0 else f"{gecikme} gün GECİKMELİ"
    print(f"[<] İade alındı (işlem #{odunc_id}): '{islem['kitap_adi']}' <- {islem['ad_soyad']} ({mesaj})")
    return gecikme


def odunc_durumu_kontrol(odunc_id, bugun=None):
    with baglanti() as conn:
        islem = conn.execute("""
            SELECT o.id, k.kitap_adi, u.ad_soyad, o.odunc_tarihi, o.son_teslim_tarihi, o.iade_tarihi
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
    print(f"[{'!!' if geciken else 'OK'}] İşlem #{odunc_id}: '{sonuc['kitap_adi']}' / {sonuc['uye']} -> {durum}")
    return sonuc


def aktif_oduncleri_listele(sadece_geciken=False, bugun=None, yazdir=True):
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
        print("\n=== GECİKEN ÖDÜNÇLER ===" if sadece_geciken else "\n=== AKTİF ÖDÜNÇ LİSTESİ ===")
        if not liste:
            print("Kayıt bulunamadı.")
        else:
            tablo = [
                (k["odunc_id"], k["kitap_adi"], k["uye"], k["odunc_tarihi"], k["son_teslim_tarihi"], k["durum"])
                for k in liste
            ]
            _tablo_yazdir(
                ["ID", "Kitap Adı", "Üye", "Ödünç T.", "Son Teslim", "Durum"],
                tablo,
                [3, 24, 18, 10, 10, 22],
            )
    return liste


def uye_oduncleri_listele(uye_id, bugun=None):
    with baglanti() as conn:
        satirlar = conn.execute("""
            SELECT o.id, k.kitap_adi, k.yazar, k.yayinevi, o.odunc_tarihi,
                   o.son_teslim_tarihi, o.iade_tarihi
            FROM odunc_islemleri o
            JOIN kitaplar k ON k.id = o.kitap_id
            WHERE o.uye_id = ?
            ORDER BY o.iade_tarihi IS NOT NULL, o.son_teslim_tarihi ASC
        """, (uye_id,)).fetchall()

    liste = []
    for s in satirlar:
        if s["iade_tarihi"] is not None:
            durum, geciken = f"İade Edildi ({s['iade_tarihi']})", False
        else:
            _, durum, geciken = odunc_durumu_hesapla(s["son_teslim_tarihi"], bugun)

        liste.append({
            "odunc_id": s["id"],
            "kitap_adi": s["kitap_adi"],
            "yazar": s["yazar"],
            "yayinevi": s["yayinevi"],
            "odunc_tarihi": s["odunc_tarihi"],
            "son_teslim_tarihi": s["son_teslim_tarihi"],
            "iade_tarihi": s["iade_tarihi"],
            "durum": durum,
            "geciken_mi": geciken,
        })
    return liste


def kutuphane_durumu_goster(bugun=None, yazdir=True):
    with baglanti() as conn:
        kitap_ozet = conn.execute(
            "SELECT COUNT(*) AS cesit, COALESCE(SUM(toplam_stok), 0) AS toplam_stok FROM kitaplar"
        ).fetchone()
        uye_sayisi = conn.execute("SELECT COUNT(*) AS adet FROM uyeler").fetchone()["adet"]
        aktif_odunc = conn.execute(
            "SELECT COUNT(*) AS adet FROM odunc_islemleri WHERE iade_tarihi IS NULL"
        ).fetchone()["adet"]

    geciken_sayisi = len(aktif_oduncleri_listele(sadece_geciken=True, bugun=bugun, yazdir=False))
    toplam_stok = kitap_ozet["toplam_stok"]

    ozet = {
        "kitap_cesidi": kitap_ozet["cesit"],
        "toplam_stok": toplam_stok,
        "oduncte": aktif_odunc,
        "rafta": toplam_stok - aktif_odunc,
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
    ornek_kitaplar = [
        ("Suç ve Ceza", "Fyodor Dostoyevski", 687, "Klasik", 4, "İş Bankası Kültür Yayınları"),
        ("Saatleri Ayarlama Enstitüsü", "Ahmet Hamdi Tanpınar", 382, "Türk Edebiyatı", 3, "Dergah Yayınları"),
        ("1984", "George Orwell", 352, "Distopya", 5, "Can Yayınları"),
        ("Kürk Mantolu Madonna", "Sabahattin Ali", 160, "Türk Edebiyatı", 4, "Yapı Kredi Yayınları"),
        ("Sefiller", "Victor Hugo", 1463, "Klasik", 2, "İş Bankası Kültür Yayınları"),
        ("Simyacı", "Paulo Coelho", 188, "Roman", 3, "Can Yayınları"),
    ]
    ornek_uyeler = [
        ("Ahmet Yılmaz", "0532 111 22 33", "ahmet.yilmaz@ornek.com"),
        ("Zeynep Kaya", "0544 222 33 44", "zeynep.kaya@ornek.com"),
        ("Mehmet Demir", "0555 333 44 55", "mehmet.demir@ornek.com"),
        ("Elif Çelik", "0533 444 55 66", "elif.celik@ornek.com"),
    ]
    ornek_oduncler = [
        ("Suç ve Ceza", "ahmet.yilmaz@ornek.com", 20),
        ("Saatleri Ayarlama Enstitüsü", "ahmet.yilmaz@ornek.com", 5),
        ("1984", "zeynep.kaya@ornek.com", 14),
    ]

    eklenen_kitap = 0
    for ad, yazar, sayfa, kat, stok, yayinevi in ornek_kitaplar:
        try:
            kitap_ekle(ad, yazar, sayfa, kat, stok, yayinevi=yayinevi)
            eklenen_kitap += 1
        except KutuphaneHatasi:
            pass

    eklenen_uye = 0
    for ad_soyad, tel, eposta in ornek_uyeler:
        try:
            uye_ekle(ad_soyad, tel, eposta, sifre="123456", rol="uye")
            eklenen_uye += 1
        except KutuphaneHatasi:
            pass

    islemler = []
    with baglanti() as conn:
        kitap_idleri = {s["kitap_adi"].casefold(): s["id"] for s in conn.execute("SELECT id, kitap_adi FROM kitaplar")}
        uye_idleri = {s["eposta"].casefold(): s["id"] for s in conn.execute("SELECT id, eposta FROM uyeler")}
        for kitap_adi, eposta, once in ornek_oduncler:
            kid, uid = kitap_idleri.get(kitap_adi.casefold()), uye_idleri.get(eposta.casefold())
            if kid is None or uid is None:
                continue
            kayit_var = conn.execute(
                "SELECT 1 FROM odunc_islemleri WHERE kitap_id = ? AND uye_id = ?", (kid, uid)
            ).fetchone()
            if not kayit_var:
                islemler.append((kid, uid, once))

    bugun = date.today()
    for kid, uid, once in islemler:
        try:
            odunc_ver(kid, uid, gun_sayisi=14, odunc_tarihi=bugun - timedelta(days=once))
        except KutuphaneHatasi:
            pass

    print(f"[*] Örnek veri yükleme tamamlandı ({eklenen_kitap} yeni kitap, {eklenen_uye} yeni üye).")


def _sayi_iste(mesaj, bos_gecilebilir=False):
    deger = input(mesaj).strip()
    if bos_gecilebilir and deger == "":
        return None
    try:
        return int(deger)
    except ValueError:
        raise KutuphaneHatasi("Lütfen geçerli bir tam sayı girin.")


def ana_menu():
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
                yayinevi = input("Yayınevi     : ") or "Belirtilmemiş"
                sayfa = _sayi_iste("Sayfa Sayısı : ")
                kategori = input("Kategori     : ")
                stok = _sayi_iste("Toplam Stok  : ")
                kitap_ekle(ad, yazar, sayfa, kategori, stok, yayinevi=yayinevi)

            elif secim == "3":
                print("\n--- KİTAP GÜNCELLE (Değiştirmek istemediğiniz alanı boş bırakın) ---")
                kitaplari_listele()
                kid = _sayi_iste("\nGüncellenecek Kitap ID: ")
                ad = input("Yeni Kitap Adı    : ").strip() or None
                yazar = input("Yeni Yazar        : ").strip() or None
                yayinevi = input("Yeni Yayınevi     : ").strip() or None
                sayfa = _sayi_iste("Yeni Sayfa Sayısı : ", bos_gecilebilir=True)
                kategori = input("Yeni Kategori     : ").strip() or None
                stok = _sayi_iste("Yeni Toplam Stok  : ", bos_gecilebilir=True)
                kitap_guncelle(kid, kitap_adi=ad, yazar=yazar, sayfa_sayisi=sayfa,
                               kategori=kategori, toplam_stok=stok, yayinevi=yayinevi)

            elif secim == "4":
                print("\n--- KİTAP SİL ---")
                kitaplari_listele()
                kitap_sil(_sayi_iste("\nSilinecek Kitap ID: "))

            elif secim == "5":
                uyeleri_listele()

            elif secim == "6":
                print("\n--- YENİ ÜYE EKLE ---")
                ad_soyad = input("Ad Soyad               : ")
                telefon = input("Telefon (isteğe bağlı) : ")
                eposta = input("E-posta (zorunlu)      : ")
                sifre = input("Şifre (varsayılan 123456): ").strip() or "123456"
                uye_ekle(ad_soyad, telefon, eposta, sifre=sifre)

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
                iade_al(_sayi_iste("\nİade Alınacak Ödünç İşlem ID: "))

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