# UltraVideoDownloader — build automático do APK via GitHub Actions

Este repositório está pronto para compilar o APK Android **na nuvem**,
sem precisar instalar Android SDK/NDK/Buildozer na sua máquina.

## Passo a passo (uma vez só)

1. **Crie um repositório novo no GitHub** (pode ser privado):
   https://github.com/new

2. **Suba estes arquivos para o repositório.** Na pasta onde você
   descompactou este projeto, rode:
   ```bash
   git init
   git add .
   git commit -m "UltraVideoDownloader mobile"
   git branch -M main
   git remote add origin https://github.com/SEU_USUARIO/SEU_REPO.git
   git push -u origin main
   ```
   (Substitua pela URL do repositório que você criou no passo 1. Se
   preferir, dá pra fazer isso direto pela interface web do GitHub,
   arrastando os arquivos em "Add file → Upload files".)

3. **O build começa sozinho.** Assim que o push terminar, vá na aba
   **Actions** do repositório no GitHub — o workflow "Build Android APK"
   já vai estar rodando (ou prestes a começar). O primeiro build demora
   entre 20 e 40 minutos (baixa e configura o Android SDK/NDK do zero).
   Os próximos builds são bem mais rápidos por causa do cache.

4. **Baixe o APK pronto.** Quando o workflow terminar com o ✅ verde,
   clique nele → role até **Artifacts** → baixe o arquivo
   `UltraVideoDownloader-apk.zip`. Dentro dele está o `.apk`.

5. **Transfira o APK para o celular** (Google Drive, WhatsApp, cabo USB,
   e-mail — qualquer forma de mover o arquivo) e instale, permitindo
   "fontes desconhecidas" quando o Android pedir.

## Quando eu preciso rodar de novo?

Todo `git push` na branch `main` que altere algo dentro de
`UltraVideoDownloader_Mobile/` dispara um novo build automaticamente. Você
também pode disparar manualmente pela aba Actions → "Build Android APK" →
"Run workflow".

## Estrutura

```
.github/workflows/build-apk.yml   → workflow do GitHub Actions
UltraVideoDownloader_Mobile/
  core.py            núcleo (validação de URL, sanitização, fila, histórico)
  main.py            app Kivy
  ultradl.kv         layout das telas
  buildozer.spec     configuração de build do APK
  icon.png           ícone do app
```

## Limitações (leia antes de compilar)

- **FFmpeg** está incluído no `buildozer.spec` para permitir extração de
  áudio (MP3). Isso deixa o build mais lento e o APK maior. Se quiser um
  build mais rápido/leve, remova `ffmpeg` da linha `requirements` do
  `buildozer.spec` — o app continua funcionando, só sem MP3 e com menos
  opções de qualidade em alguns vídeos.
- **Políticas de loja de apps**: publicar na Google Play um app que baixa
  vídeos de YouTube/Instagram/TikTok pode esbarrar nas políticas da loja
  e nos Termos de Serviço dessas plataformas. Instalar o APK manualmente
  (fora da loja), como este guia descreve, não tem esse bloqueio técnico.
- **Extratores de terceiros mudam com frequência.** Se downloads
  começarem a falhar no futuro, atualize a linha `yt-dlp` no
  `requirements` do `buildozer.spec` (ex.: fixe uma versão nova) e rode
  o workflow de novo.
