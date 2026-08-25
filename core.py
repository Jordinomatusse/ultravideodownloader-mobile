# core.py - Núcleo do UltraVideoDownloader (sem interface)
"""
Lógica compartilhada do UltraVideoDownloader, independente de UI.

Este módulo é usado pelo app mobile (Kivy/Android) em main.py. Não importa
tkinter nem nada específico de desktop, e não assume `Path.home()` (que não
é confiável no Android) — quem instancia AppConfig/DownloadHistory decide
onde os arquivos ficam (normalmente App.user_data_dir do Kivy).

Regras de segurança mantidas da versão desktop:
- is_valid_download_url(): só aceita http/https com host (bloqueia
  file://, javascript:, data: etc.)
- sanitize_filename(): remove separadores de caminho, "..", caracteres de
  controle e nomes reservados do Windows (o pacote pode rodar em qualquer
  SO durante testes/desenvolvimento).
- Erros são sempre registrados via `logging` em vez de silenciados com
  `except: pass`.
"""

import json
import logging
import os
import threading
import uuid
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple
from urllib.parse import urlparse

try:
    import yt_dlp
except ImportError:  # checado explicitamente antes de iniciar o app
    yt_dlp = None

logger = logging.getLogger("UltraVideoDownloader")

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Linux; Android 13; Mobile) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",
}

ALLOWED_URL_SCHEMES = {"http", "https"}
MAX_THUMBNAIL_BYTES = 5 * 1024 * 1024  # 5 MB

WINDOWS_RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


def is_valid_download_url(url: str) -> bool:
    """Aceita apenas URLs http(s) com host definido, bloqueando esquemas
    perigosos como file://, javascript: ou data:."""
    if not url:
        return False
    try:
        parsed = urlparse(url.strip())
    except ValueError:
        return False
    return parsed.scheme in ALLOWED_URL_SCHEMES and bool(parsed.netloc)


def sanitize_filename(filename: str, max_length: int = 150) -> str:
    """Remove caracteres inválidos/perigosos de um nome de arquivo,
    prevenindo path traversal e nomes reservados do Windows."""
    if not filename:
        return "download"

    filename = filename.replace("/", "_").replace("\\", "_")
    filename = "".join(ch for ch in filename if ch.isprintable())

    for ch in '<>:"|?*\x00':
        filename = filename.replace(ch, "")

    filename = filename.replace("..", "_")
    filename = filename.strip(" .")

    if not filename:
        filename = "download"

    if filename.upper() in WINDOWS_RESERVED_NAMES:
        filename = f"_{filename}"

    return filename[:max_length]


# ============================================================================
# MODELOS DE DADOS
# ============================================================================

@dataclass
class DownloadItem:
    id: str
    url: str
    title: str
    quality: str
    format_type: str
    save_path: str
    custom_filename: str = ""
    status: str = "pending"
    progress: float = 0.0
    downloaded_size: str = "0 MB"
    total_size: str = "0 MB"
    speed: str = "0 MB/s"
    eta: str = "--:--"
    error_msg: str = ""
    thumbnail: Optional[str] = None
    duration: str = ""
    file_size: int = 0


# ============================================================================
# CONFIGURAÇÃO PERSISTENTE
# ============================================================================

class AppConfig:
    """Preferências do usuário (tema, qualidade padrão), gravadas de forma
    atômica com tratamento de erro específico."""

    DEFAULTS = {
        "theme": "dark",
        "quality": "1080p",
    }

    def __init__(self, config_file: Path):
        self.config_file = Path(config_file)
        self.data: Dict = dict(self.DEFAULTS)
        self.load()

    def load(self) -> None:
        if not self.config_file.exists():
            return
        try:
            with open(self.config_file, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                self.data.update(loaded)
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Falha ao carregar configuração (%s), usando padrões.", exc)

    def save(self) -> None:
        try:
            self.config_file.parent.mkdir(parents=True, exist_ok=True)
            tmp_file = self.config_file.with_suffix(".tmp")
            with open(tmp_file, "w", encoding="utf-8") as f:
                json.dump(self.data, f, ensure_ascii=False, indent=2)
            tmp_file.replace(self.config_file)
        except OSError as exc:
            logger.warning("Falha ao salvar configuração: %s", exc)

    def get(self, key, default=None):
        return self.data.get(key, default)

    def set(self, key, value) -> None:
        self.data[key] = value


# ============================================================================
# HISTÓRICO DE DOWNLOADS
# ============================================================================

class DownloadHistory:
    def __init__(self, history_file: Path):
        self.history_file = Path(history_file)
        self.history: List[Dict] = []
        self.load()

    def load(self) -> None:
        if not self.history_file.exists():
            return
        try:
            with open(self.history_file, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            self.history = loaded if isinstance(loaded, list) else []
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Histórico corrompido ou ilegível (%s), começando vazio.", exc)
            self.history = []

    def save(self) -> None:
        try:
            self.history_file.parent.mkdir(parents=True, exist_ok=True)
            tmp_file = self.history_file.with_suffix(".tmp")
            with open(tmp_file, "w", encoding="utf-8") as f:
                json.dump(self.history[-500:], f, ensure_ascii=False, indent=2)
            tmp_file.replace(self.history_file)
        except OSError as exc:
            logger.warning("Falha ao salvar histórico: %s", exc)

    def add(self, item: DownloadItem, file_path: str) -> None:
        entry = {
            "id": item.id,
            "url": item.url,
            "title": item.title,
            "quality": item.quality,
            "format_type": item.format_type,
            "file_path": file_path,
            "download_date": datetime.now().isoformat(),
            "file_size_mb": item.file_size / (1024 * 1024) if item.file_size else 0,
        }
        self.history.insert(0, entry)
        self.save()

    def get_recent(self, limit: int = 30) -> List[Dict]:
        return self.history[:limit]


# ============================================================================
# NÚCLEO DO DOWNLOADER
# ============================================================================

class UltraDownloaderCore:
    def __init__(self):
        self.progress_callback: Optional[Callable] = None
        self.current_download: Optional[DownloadItem] = None
        self.cancel_requested = False

    def cancel(self):
        self.cancel_requested = True

    def download(self, item: DownloadItem) -> Tuple[bool, str]:
        self.current_download = item
        self.cancel_requested = False

        if not is_valid_download_url(item.url):
            logger.warning("URL rejeitada por validação de segurança: %s", item.url)
            return False, "URL inválida. Use apenas links http:// ou https://."

        if yt_dlp is None:
            return False, "yt-dlp não está instalado neste dispositivo."

        try:
            ydl_opts = {
                'quiet': True,
                'no_warnings': True,
                'progress_hooks': [self._progress_hook],
                'socket_timeout': 60,
                'retries': 20,
                'fragment_retries': 20,
                'headers': DEFAULT_HEADERS,
                'extract_flat': False,
                'ignoreerrors': True,
                'restrictfilenames': True,
                'windowsfilenames': True,
            }

            url_lower = item.url.lower()

            if 'instagram.com' in url_lower:
                ydl_opts['extractor_args'] = {
                    'instagram': {'user_agent': [DEFAULT_HEADERS['User-Agent']], 'login': ['false']}
                }
                if item.format_type == 'audio' or item.quality == 'audio':
                    ydl_opts['format'] = 'bestaudio/best'
                    ydl_opts['postprocessors'] = [{
                        'key': 'FFmpegExtractAudio', 'preferredcodec': 'mp3', 'preferredquality': '192'
                    }]
                else:
                    ydl_opts['format'] = 'best[height<=1080]/best'
                    ydl_opts['merge_output_format'] = 'mp4'

            elif 'tiktok.com' in url_lower:
                ydl_opts['format'] = 'best'
                ydl_opts['merge_output_format'] = 'mp4'

            else:
                if item.format_type == 'audio' or item.quality == 'audio':
                    ydl_opts['format'] = 'bestaudio/best'
                    ydl_opts['postprocessors'] = [{
                        'key': 'FFmpegExtractAudio', 'preferredcodec': 'mp3', 'preferredquality': '320'
                    }]
                else:
                    quality_map = {
                        '2160p': 'bestvideo[height<=2160]+bestaudio/best[height<=2160]',
                        '1440p': 'bestvideo[height<=1440]+bestaudio/best[height<=1440]',
                        '1080p': 'bestvideo[height<=1080]+bestaudio/best[height<=1080]',
                        '720p': 'bestvideo[height<=720]+bestaudio/best[height<=720]',
                        '480p': 'bestvideo[height<=480]+bestaudio/best[height<=480]',
                        '360p': 'bestvideo[height<=360]+bestaudio/best[height<=360]',
                        'best': 'bestvideo+bestaudio/best'
                    }
                    ydl_opts['format'] = quality_map.get(item.quality, 'best')
                    ydl_opts['merge_output_format'] = 'mp4'

            save_path = Path(item.save_path)
            save_path.mkdir(parents=True, exist_ok=True)

            if item.custom_filename:
                filename = sanitize_filename(item.custom_filename)
                template = str(save_path / f"{filename}.%(ext)s")
            elif 'instagram.com' in url_lower:
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                template = str(save_path / f"instagram_{timestamp}.%(ext)s")
            elif 'tiktok.com' in url_lower:
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                template = str(save_path / f"tiktok_{timestamp}.%(ext)s")
            else:
                template = str(save_path / "%(title)s.%(ext)s")

            ydl_opts['outtmpl'] = template

            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(item.url, download=False)

                if self.cancel_requested:
                    return False, "Download cancelado pelo usuário"
                if not info:
                    return False, "Não foi possível extrair informações do vídeo"

                if not item.title:
                    if 'instagram.com' in url_lower:
                        item.title = f"{info.get('uploader', 'Instagram')}_video"
                    elif 'tiktok.com' in url_lower:
                        item.title = f"{info.get('uploader', 'TikTok')}_video"
                    else:
                        item.title = info.get('title', 'Vídeo')

                ydl.download([item.url])

                downloaded_file = ydl.prepare_filename(info)

                if not os.path.exists(downloaded_file):
                    video_files = list(save_path.glob("*.mp4")) + list(save_path.glob("*.webm"))
                    if video_files:
                        downloaded_file = str(max(video_files, key=os.path.getctime))

                if item.format_type == 'audio' or item.quality == 'audio':
                    base = os.path.splitext(downloaded_file)[0]
                    mp3_file = base + '.mp3'
                    if os.path.exists(mp3_file):
                        downloaded_file = mp3_file

                if os.path.exists(downloaded_file):
                    return True, downloaded_file
                return False, "Arquivo não encontrado após download"

        except Exception as exc:
            logger.exception("Falha ao baixar %s", item.url)
            return False, str(exc)

    def _progress_hook(self, d):
        if self.cancel_requested:
            raise Exception("Download cancelado")

        if self.progress_callback and d['status'] == 'downloading':
            percent = 0.0
            downloaded_mb = 0.0
            total_mb = 0.0
            speed_mb = 0.0

            if d.get('total_bytes'):
                percent = (d['downloaded_bytes'] / d['total_bytes']) * 100
                downloaded_mb = d['downloaded_bytes'] / (1024 * 1024)
                total_mb = d['total_bytes'] / (1024 * 1024)
            elif d.get('total_bytes_estimate'):
                percent = (d['downloaded_bytes'] / d['total_bytes_estimate']) * 100
                downloaded_mb = d['downloaded_bytes'] / (1024 * 1024)
                total_mb = d['total_bytes_estimate'] / (1024 * 1024)

            speed = d.get('speed', 0)
            if speed:
                speed_mb = speed / (1024 * 1024)
            eta = d.get('eta', 0)

            self.progress_callback(
                percent=percent,
                downloaded=f"{downloaded_mb:.1f} MB",
                total=f"{total_mb:.1f} MB",
                speed=f"{speed_mb:.1f} MB/s",
                eta=f"{eta:.0f}s" if eta else "--:--"
            )

    def get_video_info(self, url: str) -> Optional[Dict]:
        if not is_valid_download_url(url):
            logger.warning("URL rejeitada por validação de segurança: %s", url)
            return None
        if yt_dlp is None:
            return None

        try:
            ydl_opts = {
                'quiet': True, 'no_warnings': True, 'extract_flat': False,
                'headers': DEFAULT_HEADERS, 'ignoreerrors': True,
            }
            url_lower = url.lower()
            if 'instagram.com' in url_lower:
                ydl_opts['extractor_args'] = {
                    'instagram': {'user_agent': [DEFAULT_HEADERS['User-Agent']], 'login': ['false']}
                }

            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)
                if not info:
                    return None

                if 'entries' in info and info['entries']:
                    first = info['entries'][0]
                    if first:
                        info = first

                formats = []
                if 'instagram.com' in url_lower:
                    for f in info.get('formats', []):
                        if f.get('vcodec') != 'none':
                            height = f.get('height', 0)
                            if height and height > 0:
                                formats.append({'height': height, 'quality': f"{height}p",
                                                 'ext': f.get('ext', 'mp4'), 'format_id': f.get('format_id')})
                    if not formats:
                        formats = [{'height': 720, 'quality': '720p', 'ext': 'mp4', 'format_id': 'best'}]
                else:
                    seen = set()
                    for f in info.get('formats', []):
                        height = f.get('height')
                        if height and height > 0 and height not in seen:
                            seen.add(height)
                            formats.append({'height': height, 'quality': f"{height}p",
                                             'ext': f.get('ext', 'mp4'), 'format_id': f.get('format_id')})

                formats.sort(key=lambda x: x['height'], reverse=True)

                thumbnail = info.get('thumbnail', '')
                if not thumbnail and 'instagram.com' in url_lower and info.get('thumbnails'):
                    thumbnail = info['thumbnails'][-1].get('url', '')

                return {
                    'title': info.get('title', info.get('uploader', 'Vídeo')),
                    'thumbnail': thumbnail,
                    'duration': self._format_duration(info.get('duration', 0)),
                    'formats': formats,
                    'uploader': info.get('uploader', ''),
                    'description': (info.get('description') or '')[:200],
                }
        except Exception:
            logger.exception("Erro ao obter informações de %s", url)
            return None

    @staticmethod
    def _format_duration(seconds):
        if not seconds or seconds <= 0:
            return "00:00"
        minutes = int(seconds) // 60
        remaining_seconds = int(seconds) % 60
        return f"{minutes}:{remaining_seconds:02d}"


# ============================================================================
# GERENCIADOR DE FILA
# ============================================================================

class DownloadQueueManager:
    def __init__(self, core: UltraDownloaderCore, history: DownloadHistory):
        self.core = core
        self.queue: "deque[DownloadItem]" = deque()
        self.active: Optional[DownloadItem] = None
        self.is_processing = False
        self.history = history
        self.listeners: List[Callable] = []

    def add_listener(self, callback):
        self.listeners.append(callback)

    def _notify(self, status=None):
        if status is None:
            status = self.get_status()
        for callback in self.listeners:
            try:
                callback(status)
            except Exception:
                logger.exception("Erro ao notificar listener da fila")

    def add(self, item: DownloadItem):
        item.id = uuid.uuid4().hex[:8]
        self.queue.append(item)
        self._notify()
        self._process()

    def get_status(self) -> Dict:
        return {'queue_size': len(self.queue), 'active': self.active, 'is_processing': self.is_processing}

    def _process(self):
        if self.is_processing or not self.queue:
            return

        self.is_processing = True
        self.active = self.queue.popleft()
        self.active.status = "downloading"
        self._notify()

        def run_download():
            def progress_cb(percent, downloaded, total, speed, eta):
                if self.active:
                    self.active.progress = percent
                    self.active.downloaded_size = downloaded
                    self.active.total_size = total
                    self.active.speed = speed
                    self.active.eta = eta
                    self._notify()

            self.core.progress_callback = progress_cb
            success, result = self.core.download(self.active)

            if success:
                self.active.status = "completed"
                self.active.progress = 100
                if os.path.exists(result):
                    self.active.file_size = os.path.getsize(result)
                self.history.add(self.active, result)
            else:
                self.active.status = "failed"
                self.active.error_msg = result
                logger.warning("Download falhou (%s): %s", self.active.url, result)

            self.active = None
            self.is_processing = False
            self._notify()
            self._process()

        threading.Thread(target=run_download, daemon=True).start()
