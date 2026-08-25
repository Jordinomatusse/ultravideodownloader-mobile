[app]
title = UltraVideoDownloader
package.name = ultravideodownloader
package.domain = org.ultravideodownloader

source.dir = .
source.include_exts = py,kv,png,jpg,atlas

version = 2.0

# Dependências Python empacotadas no APK.
# ATENÇÃO: extração de áudio (MP3) e merge de vídeo+áudio em qualidades
# separadas (>720p em muitos vídeos) exigem ffmpeg. O recipe "ffmpeg" do
# python-for-android é pesado e aumenta bastante o tempo/tamanho do build.
# Remova "ffmpeg" da lista abaixo se quiser um build mais rápido e leve
# (o app ainda funciona, só sem extração de MP3 e com formatos limitados
# a streams que já vêm com vídeo+áudio juntos).
requirements = python3,kivy==2.3.1,yt-dlp,requests,certifi,chardet,idna,urllib3,plyer,ffmpeg

orientation = portrait
fullscreen = 0

icon.filename = %(source.dir)s/icon.png

# Permissão de rede (normal, concedida na instalação, sem prompt ao usuário).
# Nenhuma permissão de armazenamento é necessária: o app salva apenas em
# sua pasta privada (App.user_data_dir).
android.permissions = INTERNET

android.api = 34
android.minapi = 24
android.ndk = 25b
android.archs = arm64-v8a, armeabi-v7a
android.allow_backup = True

p4a.branch = master

[buildozer]
log_level = 2
warn_on_root = 1
