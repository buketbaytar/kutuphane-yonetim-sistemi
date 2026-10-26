import streamlit as st
import pandas as pd
from kutuphaneaciklama import (
    veritabani_olustur, kitap_ekle, kitaplari_listele, kitap_guncelle, kitap_sil,
    uye_ekle, uyeleri_listele, odunc_ver, iade_al, aktif_oduncleri_listele,
    kutuphane_durumu_goster, ornek_verileri_yukle, KutuphaneHatasi,
    kullanici_dogrula, kitap_ara, uye_oduncleri_listele, sifre_degistir
)

st.set_page_config(
    page_title="Kurumsal Kütüphane Otomasyonu",
    page_icon="📚",
    layout="wide",
    initial_sidebar_state="expanded"
)

veritabani_olustur()

ARAMA_SECENEKLERI = {
    "Otomatik (Kitap / Yazar / ISBN)": "otomatik",
    "Kitap Adı": "kitap",
    "Yazar": "yazar",
    "ISBN": "isbn",
}

FORM_VARSAYILAN = {
    "f_ad": "",
    "f_yazar": "",
    "f_yayinevi": "",
    "f_kategori": "Genel",
    "f_sayfa": 100,
    "f_stok": 1,
    "f_isbn": "",
    "f_kapak": "",
    "api_secim": 0,
}

TR_KATLAMA = str.maketrans("çğıöşü", "cgiosu")

for anahtar, deger in {"kullanici": None, "arama_sonuclari": [], "arama_notu": None}.items():
    st.session_state.setdefault(anahtar, deger)


def arama_metni_hazirla(metin):
    metin = str(metin).replace("İ", "i").replace("I", "ı").lower()
    return metin.translate(TR_KATLAMA)


def bildir(metin, ikon="✅"):
    st.session_state["bildirim"] = (metin, ikon)


def google_api_anahtari():
    try:
        return st.secrets.get("GOOGLE_BOOKS_API_KEY")
    except Exception:
        return None


def form_sifirla():
    st.session_state["arama_sonuclari"] = []
    st.session_state["arama_notu"] = None
    for anahtar, deger in FORM_VARSAYILAN.items():
        st.session_state[anahtar] = deger


def forma_yaz(kitap):
    st.session_state["f_ad"] = kitap["kitap_adi"]
    st.session_state["f_yazar"] = kitap["yazar"]
    st.session_state["f_yayinevi"] = kitap["yayinevi"]
    st.session_state["f_kategori"] = kitap["kategori"]
    st.session_state["f_sayfa"] = int(kitap["sayfa_sayisi"] or 100)
    st.session_state["f_isbn"] = kitap["isbn"] or ""
    st.session_state["f_kapak"] = kitap["kapak_url"] or ""


def sonucu_forma_yaz():
    sonuclar = st.session_state["arama_sonuclari"]
    sira = st.session_state["api_secim"]
    if 0 <= sira < len(sonuclar):
        forma_yaz(sonuclar[sira])


def sonuc_etiketi(kitap):
    parcalar = [f"{kitap['kitap_adi']} — {kitap['yazar']}", kitap["yayinevi"]]
    if kitap["yil"]:
        parcalar.append(kitap["yil"])
    if kitap["sayfa_sayisi"]:
        parcalar.append(f"{kitap['sayfa_sayisi']} syf")
    return " | ".join(parcalar)


def giris_ekrani():
    bos1, orta, bos2 = st.columns([1, 1.2, 1])

    with orta:
        st.markdown("<h2 style='text-align: center;'>📚 Kütüphane Yönetim Portalı</h2>", unsafe_allow_html=True)
        st.markdown("<p style='text-align: center; color: gray;'>Devam etmek için kurumsal hesabınızla giriş yapın</p>", unsafe_allow_html=True)

        with st.form("login_form"):
            eposta = st.text_input("E-posta Adresi", placeholder="ornek@kutuphane.com")
            sifre = st.text_input("Şifre", type="password", placeholder="••••••••")
            giris_butonu = st.form_submit_button("Giriş Yap", use_container_width=True)

            if giris_butonu:
                try:
                    st.session_state["kullanici"] = kullanici_dogrula(eposta, sifre)
                    st.rerun()
                except KutuphaneHatasi as e:
                    st.error(str(e))

        with st.expander("ℹ️ Demo Giriş Bilgileri (Test İçin)"):
            st.markdown("""
            * **Yönetici (Admin):** `admin@kutuphane.com` | **Şifre:** `admin123`
            * **Standart Üye:** Örnek verileri yükledikten sonra `ahmet.yilmaz@ornek.com` | **Şifre:** `123456`
            """)


if st.session_state["kullanici"] is None:
    giris_ekrani()
    st.stop()


aktif_kullanici = st.session_state["kullanici"]
admin_mi = aktif_kullanici["rol"] == "admin"
rol_etiketi = "🛡️ Yönetici / Personel" if admin_mi else "👤 Standart Üye"

with st.sidebar:
    st.markdown(f"### 👋 {aktif_kullanici['ad_soyad']}")
    st.caption(f"{rol_etiketi}  \n📧 {aktif_kullanici['eposta']}")
    st.divider()

    if admin_mi:
        menu = st.selectbox("İşlem Menüsü", [
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
    else:
        menu = st.selectbox("Üye Menüsü", [
            "📖 Kitap Listesi ve İşlemleri",
            "🎒 Üzerimdeki Kitaplar"
        ])

    st.divider()
    with st.expander("🔑 Şifre Değiştir"):
        with st.form("sifre_formu", clear_on_submit=True):
            eski_sifre = st.text_input("Mevcut Şifre", type="password")
            yeni_sifre = st.text_input("Yeni Şifre", type="password")
            yeni_sifre_tekrar = st.text_input("Yeni Şifre (Tekrar)", type="password")
            if st.form_submit_button("Şifreyi Güncelle", use_container_width=True):
                if yeni_sifre != yeni_sifre_tekrar:
                    st.error("Yeni şifreler eşleşmiyor.")
                else:
                    try:
                        sifre_degistir(aktif_kullanici["id"], eski_sifre, yeni_sifre)
                        st.success("Şifreniz güncellendi.")
                    except KutuphaneHatasi as e:
                        st.error(str(e))

    if st.button("🚪 Güvenli Çıkış Yap", use_container_width=True):
        st.session_state["kullanici"] = None
        st.session_state["arama_sonuclari"] = []
        st.session_state["arama_notu"] = None
        st.session_state["form_sifirla_bayragi"] = True
        st.rerun()

bildirim = st.session_state.pop("bildirim", None)
if bildirim:
    st.toast(bildirim[0], icon=bildirim[1])


if menu == "📊 Genel Durum Raporu":
    st.header("📊 Kütüphane Genel Durum Paneli")
    ozet = kutuphane_durumu_goster(yazdir=False)

    k1, k2, k3, k4, k5, k6 = st.columns(6)
    k1.metric("📚 Kitap Çeşidi", ozet["kitap_cesidi"])
    k2.metric("📦 Toplam Stok", ozet["toplam_stok"])
    k3.metric("🟢 Rafta Mevcut", ozet["rafta"])
    k4.metric("📤 Ödünçte", ozet["oduncte"])
    k5.metric("⚠️ Gecikenler", ozet["geciken"])
    k6.metric("👥 Toplam Üye", ozet["uye_sayisi"])

    st.divider()

    col_sol, col_sag = st.columns([1.5, 1])
    with col_sol:
        st.subheader("🚨 Acil Takip Gerektiren Gecikmiş İadeler")
        gecikenler = aktif_oduncleri_listele(sadece_geciken=True, yazdir=False)
        if gecikenler:
            df_geciken = pd.DataFrame(gecikenler)[["odunc_id", "kitap_adi", "uye", "son_teslim_tarihi", "durum"]]
            df_geciken.columns = ["İşlem ID", "Kitap Adı", "Üye Adı", "Son Teslim", "Gecikme Durumu"]
            st.dataframe(df_geciken, use_container_width=True, hide_index=True)
        else:
            st.success("Harika! Şu anda teslim tarihi geçmiş hiçbir kitap bulunmuyor.")

    with col_sag:
        st.subheader("📈 Stok Kullanım Oranı")
        if ozet["toplam_stok"] > 0:
            oran = min(100, int((ozet["oduncte"] / ozet["toplam_stok"]) * 100))
            st.progress(oran / 100, text=f"Stokların %{oran} kadarı şu an okuyucularda")
            st.info(f"Toplam **{ozet['toplam_stok']}** nüshanın **{ozet['rafta']}** adedi rafta ödünç verilmeye hazır bekliyor.")
        else:
            st.warning("Henüz kütüphaneye stok girişi yapılmamış.")


elif menu == "📖 Kitap Listesi ve İşlemleri":
    st.header("📖 Kütüphane Kataloğu ve Arama")

    kitaplar = kitaplari_listele(yazdir=False)
    if not kitaplar:
        st.info("Kütüphanede henüz kayıtlı kitap bulunmuyor.")
    else:
        df = pd.DataFrame([dict(s) for s in kitaplar])
        df["rafta"] = df["toplam_stok"] - df["oduncte"]

        f_col1, f_col2, f_col3 = st.columns([2, 1, 1])
        with f_col1:
            arama = st.text_input("🔍 Kitap Adı, Yazar, Yayınevi veya ISBN Ara", placeholder="Örn: Dostoyevski, 1984, Can Yayınları...")
        with f_col2:
            secilen_kat = st.selectbox("📂 Kategori Filtresi", ["Tümü"] + sorted(df["kategori"].unique().tolist()))
        with f_col3:
            sadece_raftakiler = st.checkbox("Sadece Rafta Olanları Göster", value=False)

        arama_kolonu = (
            df[["kitap_adi", "yazar", "yayinevi", "isbn"]]
            .fillna("").astype(str).agg(" ".join, axis=1)
            .map(arama_metni_hazirla)
        )
        maske = pd.Series(True, index=df.index)
        for kelime in arama_metni_hazirla(arama).split():
            maske &= arama_kolonu.str.contains(kelime, regex=False)
        if secilen_kat != "Tümü":
            maske &= df["kategori"] == secilen_kat
        if sadece_raftakiler:
            maske &= df["rafta"] > 0
        filtreli_df = df[maske]

        st.caption(f"{len(filtreli_df)} kitap listeleniyor.")
        st.dataframe(
            filtreli_df[["id", "kapak_url", "kitap_adi", "yazar", "yayinevi", "kategori", "sayfa_sayisi", "isbn", "toplam_stok", "oduncte", "rafta"]],
            use_container_width=True,
            hide_index=True,
            column_config={
                "id": st.column_config.NumberColumn("ID", width="small"),
                "kapak_url": st.column_config.ImageColumn("Kapak", width="small"),
                "kitap_adi": "Kitap Adı",
                "yazar": "Yazar",
                "yayinevi": "Yayınevi",
                "kategori": "Kategori",
                "sayfa_sayisi": "Sayfa",
                "isbn": "ISBN",
                "toplam_stok": "Toplam",
                "oduncte": "Ödünçte",
                "rafta": "Rafta"
            }
        )

        if admin_mi:
            st.divider()
            st.subheader("🛠️ Kitap Yönetim İşlemleri (Yönetici Özel)")
            tab_guncelle, tab_sil = st.tabs(["✏️ Kitap Bilgisi / Stok Güncelle", "🗑️ Kitap Sil"])

            kitap_secenek = {f"#{k['id']} - {k['kitap_adi']} ({k['yazar']})": dict(k) for k in kitaplar}

            with tab_guncelle:
                secilen_g_str = st.selectbox("Güncellenecek Kitabı Seçin", list(kitap_secenek.keys()), key="guncelle_sec")
                secilen_k = kitap_secenek[secilen_g_str]

                with st.form("kitap_guncelle_form"):
                    g_col1, g_col2 = st.columns(2)
                    yeni_ad = g_col1.text_input("Kitap Adı", value=secilen_k["kitap_adi"])
                    yeni_yazar = g_col2.text_input("Yazar", value=secilen_k["yazar"])
                    yeni_yayinevi = g_col1.text_input("Yayınevi", value=secilen_k["yayinevi"])
                    yeni_kategori = g_col2.text_input("Kategori", value=secilen_k["kategori"])
                    yeni_sayfa = g_col1.number_input("Sayfa Sayısı", min_value=1, value=int(secilen_k["sayfa_sayisi"]))
                    yeni_stok = g_col2.number_input("Toplam Stok", min_value=0, value=int(secilen_k["toplam_stok"]))

                    if st.form_submit_button("Değişiklikleri Kaydet", use_container_width=True):
                        try:
                            kitap_guncelle(
                                secilen_k["id"], kitap_adi=yeni_ad, yazar=yeni_yazar,
                                yayinevi=yeni_yayinevi, kategori=yeni_kategori,
                                sayfa_sayisi=int(yeni_sayfa), toplam_stok=int(yeni_stok)
                            )
                        except KutuphaneHatasi as e:
                            st.error(str(e))
                        else:
                            bildir("Kitap bilgileri güncellendi.")
                            st.rerun()

            with tab_sil:
                secilen_s_str = st.selectbox("Silinecek Kitabı Seçin", list(kitap_secenek.keys()), key="sil_sec")
                silinecek_k = kitap_secenek[secilen_s_str]
                st.warning(f"**Dikkat:** '{silinecek_k['kitap_adi']}' adlı kitabı kütüphaneden kalıcı olarak silmek üzeresiniz.")
                onay = st.checkbox("Bu kitabın kalıcı olarak silinmesini onaylıyorum", key=f"sil_onay_{silinecek_k['id']}")
                if st.button("🗑️ Seçili Kitabı Sil", type="primary", disabled=not onay):
                    try:
                        kitap_sil(silinecek_k["id"])
                    except KutuphaneHatasi as e:
                        st.error(str(e))
                    else:
                        bildir("Kitap silindi.", "🗑️")
                        st.rerun()


elif menu == "➕ Yeni Kitap Ekle":
    st.header("➕ Yeni Kitap Kaydı")

    if st.session_state.pop("form_sifirla_bayragi", False) or "f_ad" not in st.session_state:
        form_sifirla()

    st.subheader("🔎 1. Adım: Kitap Ara (İsteğe Bağlı)")
    with st.form("arama_formu"):
        a1, a2 = st.columns([3, 1.3])
        sorgu = a1.text_input("Kitap adı, yazar veya ISBN", placeholder="Örn: Dostoyevski, Suç ve Ceza, 9789750802942")
        tur_etiketi = a2.selectbox("Arama türü", list(ARAMA_SECENEKLERI))
        ara = st.form_submit_button("🔍 Ara", use_container_width=True)

    if ara:
        if not sorgu.strip():
            st.warning("Lütfen aranacak bir kitap adı, yazar veya ISBN girin.")
        else:
            with st.spinner("Kitaplar aranıyor..."):
                try:
                    bulunanlar, not_metni = kitap_ara(
                        sorgu, tur=ARAMA_SECENEKLERI[tur_etiketi], api_anahtari=google_api_anahtari()
                    )
                except KutuphaneHatasi as e:
                    st.error(str(e))
                else:
                    st.session_state["arama_sonuclari"] = bulunanlar
                    st.session_state["arama_notu"] = not_metni
                    st.session_state["api_secim"] = 0
                    if bulunanlar:
                        forma_yaz(bulunanlar[0])
                    else:
                        st.warning("Aramanızla eşleşen kitap bulunamadı. Bilgileri aşağıdan elle girebilirsiniz.")

    if st.session_state["arama_notu"]:
        st.info(st.session_state["arama_notu"])
        if not google_api_anahtari():
            st.caption("İpucu: Ücretsiz bir Google Books API anahtarı alıp `.streamlit/secrets.toml` dosyasına `GOOGLE_BOOKS_API_KEY = \"...\"` olarak eklerseniz bu hata kalkar.")

    sonuclar = st.session_state["arama_sonuclari"]
    secili = None
    if sonuclar:
        if st.session_state.get("api_secim", 0) >= len(sonuclar):
            st.session_state["api_secim"] = 0
        st.selectbox(
            f"Bulunan sonuçlar ({len(sonuclar)}) — seçtiğiniz kitap aşağıdaki forma otomatik yazılır",
            options=list(range(len(sonuclar))),
            format_func=lambda i: f"{i + 1}. {sonuc_etiketi(sonuclar[i])}",
            key="api_secim",
            on_change=sonucu_forma_yaz,
        )
        secili = sonuclar[st.session_state["api_secim"]]
        st.caption(f"Kaynak: {secili['kaynak']}")

    st.subheader("📝 2. Adım: Kitap Bilgilerini Kontrol Et ve Kaydet")
    if secili and not secili["sayfa_sayisi"]:
        st.caption("⚠️ Seçilen kayıtta sayfa sayısı yok; lütfen elle kontrol edin.")

    form_col, kapak_col = st.columns([3, 1])

    with form_col:
        with st.form("kitap_formu"):
            f1, f2 = st.columns(2)
            ad = f1.text_input("Kitap Adı *", key="f_ad")
            yazar = f2.text_input("Yazar *", key="f_yazar")
            yayinevi = f1.text_input("Yayınevi", key="f_yayinevi")
            kategori = f2.text_input("Kategori *", key="f_kategori")
            sayfa = f1.number_input("Sayfa Sayısı *", min_value=1, step=1, key="f_sayfa")
            stok = f2.number_input("Toplam Stok Adedi *", min_value=1, step=1, key="f_stok")
            isbn = f1.text_input("ISBN Numarası", key="f_isbn")
            kapak_url = f2.text_input("Kapak Görseli URL", key="f_kapak")

            kaydet = st.form_submit_button("💾 Kitabı Kütüphaneye Kaydet", use_container_width=True)

            if kaydet:
                try:
                    kitap_ekle(
                        ad, yazar, int(sayfa), kategori, int(stok),
                        yayinevi=yayinevi, isbn=isbn, kapak_url=kapak_url
                    )
                except KutuphaneHatasi as e:
                    st.error(str(e))
                else:
                    bildir(f"'{ad}' kütüphane envanterine eklendi.")
                    st.session_state["form_sifirla_bayragi"] = True
                    st.rerun()

    with kapak_col:
        st.markdown("**Kapak Önizlemesi**")
        kapak_onizleme = st.session_state.get("f_kapak", "")
        if kapak_onizleme:
            try:
                st.image(kapak_onizleme, width=160)
            except Exception:
                st.info("Kapak görseli yüklenemedi.")
        else:
            st.info("Kapak görseli bulunmuyor.")


elif menu == "👥 Üye Listesi":
    st.header("👥 Kayıtlı Üye ve Personel Listesi")
    uyeler = uyeleri_listele(yazdir=False)
    if uyeler:
        df = pd.DataFrame([dict(u) for u in uyeler])
        st.dataframe(
            df,
            use_container_width=True,
            hide_index=True,
            column_config={
                "id": "Üye ID",
                "ad_soyad": "Ad Soyad",
                "rol": "Sistem Rolü",
                "telefon": "Telefon",
                "eposta": "E-posta Adresi",
                "kayit_tarihi": "Kayıt Tarihi",
                "aktif_kitap": "Üzerindeki Kitap Sayısı"
            }
        )
    else:
        st.info("Kayıtlı üye bulunmuyor.")


elif menu == "👤 Yeni Üye Ekle":
    st.header("👤 Yeni Üye veya Personel Kaydı")
    with st.form("uye_formu"):
        u1, u2 = st.columns(2)
        ad_soyad = u1.text_input("Ad Soyad *")
        eposta = u2.text_input("E-posta Adresi (Giriş İçin Zorunlu) *")
        telefon = u1.text_input("Telefon (İsteğe Bağlı)")
        sifre = u2.text_input("Başlangıç Şifresi *", value="123456")
        rol_secim = st.selectbox("Yetki Rolü", ["uye (Standart Üye - Sadece Arama ve Kendi Kitapları)", "admin (Personel / Yönetici - Tam Yetki)"])

        submit = st.form_submit_button("👤 Kullanıcıyı Kaydet", use_container_width=True)

        if submit:
            secilen_rol = "admin" if rol_secim.startswith("admin") else "uye"
            try:
                uye_ekle(ad_soyad, telefon, eposta, sifre=sifre, rol=secilen_rol)
                st.success(f"✅ '{ad_soyad}' ({secilen_rol}) hesabı başarıyla oluşturuldu!")
            except KutuphaneHatasi as e:
                st.error(str(e))


elif menu == "📤 Kitap Ödünç Ver":
    st.header("📤 Kitap Ödünç Verme İşlemi")
    kitaplar = kitaplari_listele(yazdir=False)
    uyeler = uyeleri_listele(yazdir=False)

    if kitaplar and uyeler:
        with st.container(border=True):
            kitap_secenekleri = {
                f"#{k['id']} - {k['kitap_adi']} ({k['yazar']}) | Rafta: {k['toplam_stok'] - k['oduncte']}": k['id']
                for k in kitaplar
            }
            uye_secenekleri = {
                f"#{u['id']} - {u['ad_soyad']} ({u['eposta']}) | Elindeki: {u['aktif_kitap']}": u['id']
                for u in uyeler
            }

            secilen_kitap_str = st.selectbox("📖 Ödünç Verilecek Kitabı Seçin", list(kitap_secenekleri.keys()))
            secilen_uye_str = st.selectbox("👤 Ödünç Alacak Üyeyi Seçin", list(uye_secenekleri.keys()))
            gun_sayisi = st.number_input("🗓️ Ödünç Süresi (Gün)", min_value=1, max_value=90, value=14)

            if st.button("📤 Ödünç İşlemini Tamamla", type="primary", use_container_width=True):
                try:
                    odunc_ver(kitap_secenekleri[secilen_kitap_str], uye_secenekleri[secilen_uye_str], gun_sayisi=int(gun_sayisi))
                    st.success("✅ Kitap başarıyla üyeye ödünç verildi!")
                except KutuphaneHatasi as e:
                    st.error(str(e))
    else:
        st.warning("Ödünç işlemi için sistemde en az bir kitap ve üye bulunmalıdır.")


elif menu == "📥 Kitap İade Al":
    st.header("📥 Kitap İade Alma İşlemi")
    aktif_list = aktif_oduncleri_listele(sadece_geciken=False, yazdir=False)
    if aktif_list:
        with st.container(border=True):
            aktif_secenekleri = {
                f"İşlem #{a['odunc_id']} | 📖 {a['kitap_adi']} | 👤 {a['uye']} | Durum: {a['durum']}": a['odunc_id']
                for a in aktif_list
            }
            secilen_islem_str = st.selectbox("İade Alınacak Ödünç İşlemini Seçin", list(aktif_secenekleri.keys()))

            if st.button("📥 İadeyi Onayla ve Rafa Ekle", type="primary", use_container_width=True):
                try:
                    gecikme = iade_al(aktif_secenekleri[secilen_islem_str])
                    if gecikme > 0:
                        bildir(f"Kitap iade alındı ancak {gecikme} gün gecikmeli teslim edildi.", "⚠️")
                    else:
                        bildir("Kitap zamanında iade alındı.")
                    st.rerun()
                except KutuphaneHatasi as e:
                    st.error(str(e))
    else:
        st.info("Şu an ödünçte olan aktif kitap bulunmuyor.")


elif menu == "⏱️ Aktif / Geciken Ödünçler":
    st.header("⏱️ Aktif ve Geciken Ödünç Takibi")
    secim_tipi = st.radio("Görünüm Filtresi", ["Tüm Aktif Ödünçler", "🚨 Sadece Gecikenler"], horizontal=True)

    sonuclar = aktif_oduncleri_listele(sadece_geciken=secim_tipi.startswith("🚨"), yazdir=False)
    if sonuclar:
        df = pd.DataFrame(sonuclar)
        st.dataframe(
            df[["odunc_id", "kitap_adi", "uye", "odunc_tarihi", "son_teslim_tarihi", "durum"]],
            use_container_width=True,
            hide_index=True,
            column_config={
                "odunc_id": "İşlem ID",
                "kitap_adi": "Kitap Adı",
                "uye": "Üye Adı Soyadı",
                "odunc_tarihi": "Veriliş Tarihi",
                "son_teslim_tarihi": "Son Teslim Tarihi",
                "durum": "Süre Durumu"
            }
        )
    else:
        st.info("Seçilen filtreye uygun ödünç kaydı bulunamadı.")


elif menu == "⚡ Örnek Verileri Yükle":
    st.header("⚡ Test ve Demo Verilerini Yükle")
    st.write("Sistemi hızlıca test etmek için örnek klasikleri, demo üyeleri (şifre: `123456`) ve örnek ödünç işlemlerini tek tıkla veritabanına ekleyebilirsiniz.")
    if st.button("🚀 Örnek Kitap ve Üyeleri Ekle", type="primary"):
        ornek_verileri_yukle()
        st.success("✅ Örnek veriler başarıyla yüklendi! Sol menüden katalog ve raporları inceleyebilirsiniz.")


elif menu == "🎒 Üzerimdeki Kitaplar":
    st.header("🎒 Üzerimdeki Kitaplar ve Okuma Geçmişim")

    kayitlar = uye_oduncleri_listele(aktif_kullanici["id"])
    aktif_kitaplar = [k for k in kayitlar if k["iade_tarihi"] is None]
    gecmis_kitaplar = [k for k in kayitlar if k["iade_tarihi"] is not None]
    geciken_kitaplar = [k for k in aktif_kitaplar if k["geciken_mi"]]

    m1, m2, m3 = st.columns(3)
    m1.metric("📖 Şu An Elimdeki Kitaplar", len(aktif_kitaplar))
    m2.metric("⚠️ Teslim Tarihi Geçenler", len(geciken_kitaplar))
    m3.metric("✅ Bugüne Kadar İade Ettiklerim", len(gecmis_kitaplar))

    if geciken_kitaplar:
        st.error(f"🚨 Dikkat: Teslim süresi geçmiş **{len(geciken_kitaplar)}** adet kitabınız bulunmaktadır. Lütfen en kısa sürede kütüphane görevlisine iade ediniz.")

    st.divider()
    st.subheader("📌 Şu An Üzerimde Olan Aktif Kitaplar")
    if aktif_kitaplar:
        df_aktif = pd.DataFrame(aktif_kitaplar)[["kitap_adi", "yazar", "yayinevi", "odunc_tarihi", "son_teslim_tarihi", "durum"]]
        df_aktif.columns = ["Kitap Adı", "Yazar", "Yayınevi", "Ödünç Alma Tarihi", "Son Teslim Tarihi", "Kalan Süre / Durum"]
        st.dataframe(df_aktif, use_container_width=True, hide_index=True)
    else:
        st.info("Şu anda üzerinizde ödünç alınmış aktif bir kitap bulunmuyor.")

    st.subheader("🕓 Geçmişte Okuduğum / İade Ettiğim Kitaplar")
    if gecmis_kitaplar:
        df_gecmis = pd.DataFrame(gecmis_kitaplar)[["kitap_adi", "yazar", "yayinevi", "odunc_tarihi", "iade_tarihi", "durum"]]
        df_gecmis.columns = ["Kitap Adı", "Yazar", "Yayınevi", "Alış Tarihi", "İade Tarihi", "Durum"]
        st.dataframe(df_gecmis, use_container_width=True, hide_index=True)
    else:
        st.caption("Henüz tamamlanmış bir iade geçmişiniz bulunmuyor.")