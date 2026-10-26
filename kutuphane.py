import streamlit as st
import pandas as pd
from kutuphaneaciklama import (
    veritabani_olustur, kitap_ekle, kitaplari_listele, kitap_guncelle, kitap_sil,
    uye_ekle, uyeleri_listele, odunc_ver, iade_al, aktif_oduncleri_listele,
    kutuphane_durumu_goster, ornek_verileri_yukle, KutuphaneHatasi
)

# Veritabanını başlat
veritabani_olustur()

st.set_page_config(page_title="Kütüphane Yönetim Sistemi", page_icon="📚", layout="wide")
st.title("📚 Kütüphane Yönetim ve Ödünç Takip Sistemi")

# Kenar Çubuğu Menüsü
menu = st.sidebar.selectbox("İşlem Menüsü", [
    "📊 Genel Durum Raporu",
    "📖 Kitap Listesi ve İşlemleri",
    "➕ Yeni Kitap Ekle",
    "👥 Üye Listesi",
    "👤 Yeni Üye Ekle",
    "📤 Kitap Ödünç Ver",
    "📥 Kitap İade Al",
    "⏱️ Aktif / Geciken Ödünçler",
    "⚡ Örnek Verileri Yükle"
])

if menu == "📊 Genel Durum Raporu":
    st.header("Kütüphane Genel Durum Raporu")
    if st.button("Raporu Güncelle / Göster"):
        ozet = kutuphane_durumu_goster(yazdir=False)
        col1, col2, col3 = st.columns(3)
        col1.metric("Toplam Kitap Çeşidi", ozet["kitap_cesidi"])
        col1.metric("Toplam Stok Nüshası", ozet["toplam_stok"])
        col2.metric("Şu An Ödünçte", ozet["oduncte"])
        col2.metric("Rafta Mevcut", ozet["rafta"])
        col3.metric("Geciken Kitaplar", ozet["geciken"])
        col3.metric("Kayıtlı Üye Sayısı", ozet["uye_sayisi"])

elif menu == "📖 Kitap Listesi ve İşlemleri":
    st.header("Kütüphane Kitap Listesi")
    kitaplar = kitaplari_listele(yazdir=False)
    if kitaplar:
        df = pd.DataFrame([dict(s) for s in kitaplar])
        st.dataframe(df, use_container_width=True)
    else:
        st.info("Kütüphanede kayıtlı kitap bulunmuyor.")

elif menu == "➕ Yeni Kitap Ekle":
    st.header("Yeni Kitap Kaydı Oluştur")
    with st.form("kitap_formu"):
        ad = st.text_input("Kitap Adı")
        yazar = st.text_input("Yazar")
        sayfa = st.number_input("Sayfa Sayısı", min_value=1, step=1, value=100)
        kategori = st.text_input("Kategori")
        stok = st.number_input("Toplam Stok", min_value=0, step=1, value=1)
        submit = st.form_submit_button("Kitabı Kaydet")

        if submit:
            try:
                kitap_ekle(ad, yazar, int(sayfa), kategori, int(stok))
                st.success(f"'{ad}' başarıyla eklendi!")
            except KutuphaneHatasi as e:
                st.error(str(e))

elif menu == "👥 Üye Listesi":
    st.header("Kayıtlı Üyeler")
    uyeler = uyeleri_listele(yazdir=False)
    if uyeler:
        df = pd.DataFrame([dict(u) for u in uyeler])
        st.dataframe(df, use_container_width=True)
    else:
        st.info("Kayıtlı üye bulunmuyor.")

elif menu == "👤 Yeni Üye Ekle":
    st.header("Yeni Üye Kaydı")
    with st.form("uye_formu"):
        ad_soyad = st.text_input("Ad Soyad")
        telefon = st.text_input("Telefon (İsteğe Bağlı)")
        eposta = st.text_input("E-posta (İsteğe Bağlı)")
        submit = st.form_submit_button("Üyeyi Kaydet")

        if submit:
            try:
                uye_ekle(ad_soyad, telefon, eposta)
                st.success(f"'{ad_soyad}' üyeliği oluşturuldu!")
            except KutuphaneHatasi as e:
                st.error(str(e))

elif menu == "📤 Kitap Ödünç Ver":
    st.header("Kitap Ödünç Verme İşlemi")
    kitaplar = kitaplari_listele(yazdir=False)
    uyeler = uyeleri_listele(yazdir=False)

    if kitaplar and uyeler:
        # DÜZELTME: yazar anahtarındaki eksik köşeli parantez eklendi
        kitap_secenekleri = {f"#{k['id']} - {k['kitap_adi']} ({k['yazar']})": k['id'] for k in kitaplar}
        uye_secenekleri = {f"#{u['id']} - {u['ad_soyad']}": u['id'] for u in uyeler}

        secilen_kitap_str = st.selectbox("Kitap Seçin", list(kitap_secenekleri.keys()))
        secilen_uye_str = st.selectbox("Üye Seçin", list(uye_secenekleri.keys()))
        gun_sayisi = st.number_input("Ödünç Süresi (Gün)", min_value=1, value=14)

        if st.button("Ödünç Ver"):
            try:
                kid = kitap_secenekleri[secilen_kitap_str]
                uid = uye_secenekleri[secilen_uye_str]
                odunc_ver(kid, uid, gun_sayisi=int(gun_sayisi))
                st.success("Kitap başarıyla ödünç verildi!")
            except KutuphaneHatasi as e:
                st.error(str(e))
    else:
        st.warning("Ödünç işlemi için önce sistemde en az bir kitap ve üye bulunmalıdır.")

elif menu == "📥 Kitap İade Al":
    st.header("Kitap İade İşlemi")
    aktif_list = aktif_oduncleri_listele(sadece_geciken=False, yazdir=False)
    if aktif_list:
        aktif_secenekleri = {f"İşlem #{a['odunc_id']} | Kitap: {a['kitap_adi']} | Üye: {a['uye']}": a['odunc_id'] for a
                             in aktif_list}
        secilen_islem_str = st.selectbox("İade Alınacak İşlemi Seçin", list(aktif_secenekleri.keys()))

        if st.button("İade Al"):
            try:
                oid = aktif_secenekleri[secilen_islem_str]
                gecikme = iade_al(oid)
                if gecikme > 0:
                    st.warning(f"Kitap iade alındı ancak {gecikme} gün gecikmeli!")
                else:
                    st.success("Kitap zamanında iade alındı!")
            except KutuphaneHatasi as e:
                st.error(str(e))
    else:
        st.info("Şu an ödünçte olan aktif kitap bulunmuyor.")

elif menu == "⏱️ Aktif / Geciken Ödünçler":
    st.header("Aktif ve Geciken Ödünç Takibi")
    secim_tipi = st.radio("Filtrele", ["Tüm Aktif Ödünçler", "Sadece Gecikenler"])
    sadece_gec = True if secim_tipi == "Sadece Gecikenler" else False

    # DÜZELTME: 'sadece_gececik' değişken adı 'sadece_gec' olarak düzeltildi
    sonuclar = aktif_oduncleri_listele(sadece_geciken=sadece_gec, yazdir=False)
    if sonuclar:
        df = pd.DataFrame(sonuclar)
        st.dataframe(df, use_container_width=True)
    else:
        st.info("Kayıt bulunamadı.")

elif menu == "⚡ Örnek Verileri Yükle":
    st.header("Test Verilerini Yükle")
    if st.button("Örnek Kitap ve Üyeleri Ekle"):
        ornek_verileri_yukle()
        st.success("Örnek veriler başarıyla yüklendi!")