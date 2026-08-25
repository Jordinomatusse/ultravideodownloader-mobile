# main.py - UltraVideoDownloader Mobile (Android, Kivy)
"""
Interface mobile do UltraVideoDownloader, feita em Kivy.

Decisões de segurança / armazenamento importantes:
- Os downloads são salvos em `App.user_data_dir` (pasta privada do app,
  ex.: /storage/emulated/0/Android/data/<pacote>/files no Android). Essa
  pasta NÃO exige nenhuma permissão de runtime em nenhuma versão do
  Android, então o app funciona sem pedir "Armazenamento" ao usuário.
  O arquivo baixado pode ser compartilhado para a Galeria/outro app pelo
  botão "Compartilhar" (usa o sistema de compartilhamento do Android, sem
  copiar para pastas públicas arbitrárias).
- Toda URL passa por `core.is_valid_download_url` antes de ser usada em
  qualquer lugar (inclusive antes de virar `source` de uma AsyncImage),
  bloqueando esquemas como file:// ou javascript:.
- Nomes de arquivo passam por `core.sanitize_filename`.
"""

import os
import threading
from pathlib import Path

from kivy.app import App
from kivy.clock import Clock, mainthread
from kivy.core.window import Window
from kivy.logger import Logger
from kivy.properties import BooleanProperty, ListProperty, NumericProperty, StringProperty
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.popup import Popup
from kivy.uix.screenmanager import Screen, ScreenManager, SlideTransition

from core import (
    AppConfig,
    DownloadHistory,
    DownloadItem,
    DownloadQueueManager,
    UltraDownloaderCore,
    is_valid_download_url,
)

try:
    from plyer import share as plyer_share
except ImportError:
    plyer_share = None

try:
    from android.permissions import Permission, request_permissions
    ANDROID = True
except ImportError:
    ANDROID = False

QUALITIES = [
    ("Melhor", "best"), ("4K", "2160p"), ("1080p", "1080p"),
    ("720p", "720p"), ("480p", "480p"), ("Apenas Áudio", "audio"),
]

THEMES = {
    "dark": {
        "bg": (0.06, 0.06, 0.10, 1),
        "card": (0.09, 0.09, 0.18, 1),
        "text": (1, 1, 1, 1),
        "text_secondary": (0.63, 0.63, 0.69, 1),
        "accent": (0.0, 0.82, 1.0, 1),
        "accent_2": (0.48, 0.18, 0.62, 1),
        "success": (0.0, 0.9, 0.63, 1),
        "error": (1.0, 0.28, 0.34, 1),
    },
    "light": {
        "bg": (0.96, 0.97, 0.98, 1),
        "card": (0.91, 0.93, 0.95, 1),
        "text": (0.10, 0.10, 0.18, 1),
        "text_secondary": (0.35, 0.35, 0.43, 1),
        "accent": (0.0, 0.4, 1.0, 1),
        "accent_2": (1.0, 0.2, 0.4, 1),
        "success": (0.0, 0.66, 0.42, 1),
        "error": (0.90, 0.22, 0.27, 1),
    },
}


class QueueRow(BoxLayout):
    label_text = StringProperty("")
    is_active = BooleanProperty(False)


class HistoryRow(BoxLayout):
    label_text = StringProperty("")


class MainScreen(Screen):
    pass


class HistoryScreen(Screen):
    pass


class SettingsScreen(Screen):
    pass


class UltraDLApp(App):
    theme_name = StringProperty("dark")
    bg_color = ListProperty(THEMES["dark"]["bg"])
    card_color = ListProperty(THEMES["dark"]["card"])
    text_color = ListProperty(THEMES["dark"]["text"])
    text_secondary_color = ListProperty(THEMES["dark"]["text_secondary"])
    accent_color = ListProperty(THEMES["dark"]["accent"])
    accent2_color = ListProperty(THEMES["dark"]["accent_2"])
    success_color = ListProperty(THEMES["dark"]["success"])
    error_color = ListProperty(THEMES["dark"]["error"])

    video_title = StringProperty("Nenhum vídeo selecionado")
    video_details = StringProperty("")
    thumbnail_url = StringProperty("")
    selected_quality = StringProperty("1080p")

    progress_value = NumericProperty(0)
    progress_text = StringProperty("Aguardando...")

    def build(self):
        self.title = "UltraVideoDownloader"
        Window.clearcolor = THEMES["dark"]["bg"]

        data_dir = Path(self.user_data_dir)
        self.downloads_dir = data_dir / "Downloads"
        self.downloads_dir.mkdir(parents=True, exist_ok=True)

        self.config_store = AppConfig(data_dir / "config.json")
        self.history = DownloadHistory(data_dir / "download_history.json")
        self.core = UltraDownloaderCore()
        self.queue_manager = DownloadQueueManager(self.core, self.history)
        self.queue_manager.add_listener(self._on_queue_update)

        self.selected_quality = self.config_store.get("quality", "1080p")
        self.apply_theme(self.config_store.get("theme", "dark"), persist=False)

        if ANDROID:
            # INTERNET é uma permissão "normal" (concedida na instalação);
            # nenhuma permissão de armazenamento é necessária porque
            # salvamos apenas na pasta privada do app.
            request_permissions([Permission.INTERNET])

        self.sm = ScreenManager(transition=SlideTransition())
        self.sm.add_widget(MainScreen(name="main"))
        self.sm.add_widget(HistoryScreen(name="history"))
        self.sm.add_widget(SettingsScreen(name="settings"))
        return self.sm

    def on_start(self):
        self.refresh_queue_display()
        self.refresh_history_display()

    # ------------------------------------------------------------------
    # Tema
    # ------------------------------------------------------------------

    def apply_theme(self, name: str, persist: bool = True):
        if name not in THEMES:
            name = "dark"
        palette = THEMES[name]
        self.theme_name = name
        self.bg_color = palette["bg"]
        self.card_color = palette["card"]
        self.text_color = palette["text"]
        self.text_secondary_color = palette["text_secondary"]
        self.accent_color = palette["accent"]
        self.accent2_color = palette["accent_2"]
        self.success_color = palette["success"]
        self.error_color = palette["error"]
        Window.clearcolor = palette["bg"]
        if persist:
            self.config_store.set("theme", name)
            self.config_store.save()

    def toggle_theme(self):
        self.apply_theme("light" if self.theme_name == "dark" else "dark")

    # ------------------------------------------------------------------
    # Buscar informações do vídeo
    # ------------------------------------------------------------------

    def fetch_video_info(self, url: str):
        url = (url or "").strip()
        if not is_valid_download_url(url):
            self.show_toast("Cole uma URL http(s) válida primeiro", is_error=True)
            return

        self.show_toast("Buscando informações do vídeo...")

        def worker():
            info = self.core.get_video_info(url)
            Clock.schedule_once(lambda dt: self._apply_video_info(info))

        threading.Thread(target=worker, daemon=True).start()

    @mainthread
    def _apply_video_info(self, info):
        if not info:
            self.video_title = "Não foi possível obter informações"
            self.video_details = "Verifique se o link é válido"
            self.thumbnail_url = ""
            self.show_toast("Erro ao obter informações do vídeo", is_error=True)
            return

        self.video_title = info["title"][:80]
        self.video_details = (
            f"{info.get('uploader', 'Desconhecido')}  •  "
            f"{info['duration']}  •  {len(info['formats'])} formatos"
        )
        thumb = info.get("thumbnail") or ""
        self.thumbnail_url = thumb if is_valid_download_url(thumb) else ""
        self.show_toast(f"Informações carregadas: {info['title'][:40]}", is_success=True)

    # ------------------------------------------------------------------
    # Download / fila
    # ------------------------------------------------------------------

    def set_quality(self, value: str):
        self.selected_quality = value
        self.config_store.set("quality", value)
        self.config_store.save()

    def start_download(self, url: str):
        url = (url or "").strip()
        if not is_valid_download_url(url):
            self.show_toast("Cole uma URL http(s) válida", is_error=True)
            return

        quality = self.selected_quality
        format_type = "audio" if quality == "audio" else "video"
        title = self.video_title if self.video_title not in (
            "Nenhum vídeo selecionado", "Não foi possível obter informações"
        ) else ""

        item = DownloadItem(
            id="", url=url, title=title, quality=quality, format_type=format_type,
            save_path=str(self.downloads_dir), custom_filename="",
        )
        self.queue_manager.add(item)
        self.show_toast(f"Adicionado à fila ({quality})")

    def cancel_current(self):
        self.core.cancel()
        self.show_toast("Cancelando download atual...")

    def _on_queue_update(self, status):
        Clock.schedule_once(lambda dt: self.refresh_queue_display())
        active = status.get("active")
        if status.get("is_processing") and active:
            Clock.schedule_once(lambda dt: self._update_progress(active))

    @mainthread
    def _update_progress(self, item: DownloadItem):
        self.progress_value = item.progress
        self.progress_text = f"{item.downloaded_size} / {item.total_size} • {item.speed} • {item.eta}"

    @mainthread
    def refresh_queue_display(self):
        main_screen = self.sm.get_screen("main")
        container = main_screen.ids.queue_list
        container.clear_widgets()

        active = self.queue_manager.active
        if active:
            icon = "▶" if active.status == "downloading" else "⏸"
            row = QueueRow(is_active=True)
            row.label_text = f"{icon} ATIVO: {(active.title or 'Vídeo')[:40]} ({active.quality})"
            container.add_widget(row)

        for it in self.queue_manager.queue:
            icon = {"pending": "⏳", "completed": "✓", "failed": "✗"}.get(it.status, "⏳")
            row = QueueRow(is_active=False)
            row.label_text = f"{icon} {(it.title or 'Vídeo')[:40]} ({it.quality})"
            container.add_widget(row)

        if not active and not self.queue_manager.queue:
            self.progress_value = 0
            self.progress_text = "Aguardando..."

    @mainthread
    def refresh_history_display(self):
        history_screen = self.sm.get_screen("history")
        container = history_screen.ids.history_list
        container.clear_widgets()

        for entry in self.history.get_recent(50):
            date = entry.get("download_date", "")[:16].replace("T", " ")
            title = (entry.get("title") or "Vídeo")[:45]
            row = HistoryRow()
            row.label_text = f"{date}\n{title}  ({entry.get('quality', '')})"
            container.add_widget(row)

    def clear_history(self):
        self.history.history = []
        self.history.save()
        self.refresh_history_display()
        self.show_toast("Histórico apagado")

    # ------------------------------------------------------------------
    # Compartilhar arquivo baixado (em vez de "abrir pasta", que não
    # existe como conceito em apps sandboxed no Android)
    # ------------------------------------------------------------------

    def share_last_download(self):
        recent = self.history.get_recent(1)
        if not recent:
            self.show_toast("Nenhum download concluído ainda", is_error=True)
            return

        file_path = recent[0].get("file_path", "")
        if not file_path or not os.path.exists(file_path):
            self.show_toast("Arquivo não encontrado no dispositivo", is_error=True)
            return

        if plyer_share is not None:
            try:
                plyer_share.share(
                    title="Compartilhar vídeo",
                    filepath=file_path,
                )
                return
            except Exception:
                Logger.exception("UltraVideoDownloader: falha ao compartilhar arquivo")

        self.show_toast(f"Arquivo salvo em: {file_path}")

    # ------------------------------------------------------------------
    # Navegação / avisos
    # ------------------------------------------------------------------

    def go_to(self, screen_name: str):
        if screen_name == "history":
            self.refresh_history_display()
        self.sm.current = screen_name

    def show_toast(self, message: str, is_error: bool = False, is_success: bool = False):
        accent = self.error_color if is_error else (self.success_color if is_success else self.accent_color)
        popup = Popup(
            title="", separator_height=0,
            size_hint=(0.85, None), height=90,
            background_color=(0, 0, 0, 0),
        )
        from kivy.uix.label import Label
        from kivy.uix.floatlayout import FloatLayout
        from kivy.graphics import Color, Line, RoundedRectangle

        root = FloatLayout()
        label = Label(text=message, color=accent, size_hint=(1, 1),
                       halign="center", valign="middle", padding=(16, 10))
        label.bind(size=lambda inst, val: setattr(inst, "text_size", val))

        with root.canvas.before:
            Color(*self.card_color)
            rect = RoundedRectangle(radius=[14], pos=root.pos, size=root.size)
            Color(*accent)
            border = Line(rounded_rectangle=(root.x, root.y, root.width, root.height, 14), width=1.2)

        def update_rect(instance, _value):
            rect.pos = instance.pos
            rect.size = instance.size
            border.rounded_rectangle = (instance.x, instance.y, instance.width, instance.height, 14)

        root.bind(pos=update_rect, size=update_rect)
        root.add_widget(label)
        popup.content = root
        popup.open()
        Clock.schedule_once(lambda dt: popup.dismiss(), 2.2)

    def on_stop(self):
        self.config_store.set("quality", self.selected_quality)
        self.config_store.set("theme", self.theme_name)
        self.config_store.save()


if __name__ == "__main__":
    UltraDLApp().run()
