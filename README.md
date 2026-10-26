📚 Kütüphane Yönetim ve Ödünç Takip Sistemi (Python & SQLite & Streamlit)

Bu proje, Python ve yerleşik SQLite (sqlite3) veritabanı mimarisi kullanılarak geliştirilmiş uçtan uca bir Kütüphane Yönetim ve Ödünç Takip Sistemi (CRUD) uygulamasıdır. Proje; hem arka uç (backend) veritabanı mühendisliğini ve algoritma tasarımını incelemek isteyen geliştiriciler için Komut Satırı (CLI) modunu, hem de gerçek kullanıcıların tarayıcı üzerinden etkileşimli olarak kullanabileceği Canlı Web Platformu (Streamlit) modunu bir arada sunar.

🏗️ Proje Özeti ve Mimari Özellikler

Uygulama, harici bir ORM kullanmadan doğrudan ilişkisel veritabanı kurallarını ve güvenli SQL pratiklerini sergilemek üzere tasarlanmıştır:

Atomik İşlem (Transaction) Yönetimi: @contextmanager tabanlı baglanti() yapısı sayesinde tüm veritabanı işlemleri başarılı olduğunda otomatik COMMIT edilir, herhangi bir iş kuralı veya veritabanı hatasında ise ROLLBACK yapılarak veri bütünlüğü korunur.

Eşzamanlılık ve Yarış Durumu (Race Condition) Koruması: Ödünç verme işlemlerinde BEGIN IMMEDIATE kullanılarak aynı kitabın son nüshasının aynı anda iki farklı işleme verilmesi engellenir.

SQL Injection Koruması: Tüm sorgularda parametreli sorgu yapısı (? yer tutucuları) kullanılmıştır.

Veri Bütünlüğü ve İş Kuralları (KutuphaneHatasi):

Rafta boş nüshası kalmayan kitaplar ödünç verilemez.

Bir üye, elinde zaten ödünçte bulunan aynı kitabı ikinci kez ödünç alamaz.

Üyelerde aktif ödünçte bulunan bir kitap silinemez veya toplam stoğu ödünçteki adedin altına düşürülemez.