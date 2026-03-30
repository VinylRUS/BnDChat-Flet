import asyncio
import os
import queue
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Dict, List, Optional

import flet as ft

APP_NAME = "BnDChat Flet"
ACCENT = "#FF8025"
BG = "#000000"
SURFACE = "#111111"
SURFACE_2 = "#151515"
BORDER = "#252525"
TEXT_MUTED = "#B9B9B9"
CHAT_BG = "#0C0C0C"


@dataclass
class MatrixRoom:
    room_id: str
    display_name: str


class MatrixService:
    """Matrix adapter (real + demo sandbox mode)."""

    def __init__(self):
        self.on_message: Optional[Callable[[dict], None]] = None
        self.on_rooms: Optional[Callable[[List[MatrixRoom]], None]] = None
        self.on_state: Optional[Callable[[str], None]] = None

        self.running = False
        self.connected = False
        self.demo_mode = False

        self.client = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._worker_thread: Optional[threading.Thread] = None
        self._next_batch: Optional[str] = None

        self.rooms: Dict[str, MatrixRoom] = {
            "!general:local": MatrixRoom("!general:local", "Общий чат"),
            "!admins:local": MatrixRoom("!admins:local", "Админская"),
        }

    def connect(self, homeserver: str, user: str, password: str):
        if self.running:
            self.stop()

        if self._should_use_demo(homeserver, user, password):
            self._connect_demo(user or "@sandbox-user:local")
            return

        self.running = True
        self.connected = False
        self.demo_mode = False
        self._next_batch = None

        def worker():
            async def run():
                try:
                    from nio import (
                        AsyncClient,
                        LoginError,
                        LoginResponse,
                        MatrixRoom as NioRoom,
                        RoomMessageText,
                    )
                except ImportError:
                    self._emit_state("Не найден matrix-nio. Установи: pip install matrix-nio[e2e]")
                    self.running = False
                    return

                self.client = AsyncClient(homeserver, user)
                login = await self.client.login(password=password, device_name="BnDChat Flet")

                if isinstance(login, LoginError):
                    self.running = False
                    self._emit_state(f"Ошибка Matrix login: {login.message}")
                    return
                if not isinstance(login, LoginResponse):
                    self.running = False
                    self._emit_state("Неожиданный ответ при авторизации Matrix")
                    return

                self.connected = True
                self._emit_state("Подключено")
                await self._sync_once()
                self._emit_rooms()

                def on_room_message(room: NioRoom, event: RoomMessageText):
                    if getattr(event, "decrypted", True) is False:
                        return
                    if self.on_message:
                        self.on_message(
                            {
                                "sender": event.sender,
                                "room_id": room.room_id,
                                "body": event.body,
                                "mine": event.sender == self.client.user_id,
                                "ts": datetime.now().strftime("%H:%M"),
                            }
                        )

                self.client.add_event_callback(on_room_message, RoomMessageText)

                while self.running:
                    try:
                        await self._sync_once()
                        self._emit_rooms()
                    except Exception as exc:  # keep loop alive
                        self._emit_state(f"Проблема синхронизации: {exc}")
                        await asyncio.sleep(2)

                await self.client.close()

            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            self._loop = loop
            try:
                loop.run_until_complete(run())
            finally:
                self._loop = None
                loop.close()

        self._worker_thread = threading.Thread(target=worker, daemon=True)
        self._worker_thread.start()

    def stop(self):
        self.running = False
        self.connected = False
        self.demo_mode = False
        self.client = None
        self._emit_state("Отключено")

    def _should_use_demo(self, homeserver: str, user: str, password: str) -> bool:
        hs = homeserver.strip().lower()
        return hs in {"sandbox", "demo", "mock"} or user.startswith("@sandbox") or password == "sandbox"

    def _connect_demo(self, user: str):
        self.running = True
        self.connected = True
        self.demo_mode = True
        self.client = None
        self.rooms = {
            "!general:sandbox": MatrixRoom("!general:sandbox", "Песочница / Общий"),
            "!qa:sandbox": MatrixRoom("!qa:sandbox", "Песочница / QA"),
            "!admins:sandbox": MatrixRoom("!admins:sandbox", "Песочница / Admin"),
        }
        self._emit_rooms()
        self._emit_state("Сэндбокс подключён")

        if self.on_message:
            self.on_message(
                {
                    "sender": "@sandbox-bot:local",
                    "room_id": "!general:sandbox",
                    "body": f"Сэндбокс включён. Привет, {user}!",
                    "mine": False,
                    "ts": datetime.now().strftime("%H:%M"),
                }
            )

        def bot_loop():
            heartbeat = 1
            while self.running and self.demo_mode:
                time.sleep(25)
                if self.on_message:
                    self.on_message(
                        {
                            "sender": "@sandbox-bot:local",
                            "room_id": "!qa:sandbox",
                            "body": f"Heartbeat #{heartbeat}: demo sync ok",
                            "mine": False,
                            "ts": datetime.now().strftime("%H:%M"),
                        }
                    )
                    heartbeat += 1

        threading.Thread(target=bot_loop, daemon=True).start()

    async def _sync_once(self):
        if not self.client:
            return
        response = await self.client.sync(timeout=30_000, since=self._next_batch, full_state=False)
        self._next_batch = getattr(response, "next_batch", self._next_batch)

        for room_id, room in self.client.rooms.items():
            name = room.display_name or room_id
            self.rooms[room_id] = MatrixRoom(room_id=room_id, display_name=name)

    def _emit_rooms(self):
        if self.on_rooms:
            self.on_rooms(list(self.rooms.values()))

    def _emit_state(self, text: str):
        if self.on_state:
            self.on_state(text)

    def send_message(self, room_id: str, text: str, sender: str):
        if not text.strip() or not room_id:
            return

        if self.demo_mode:
            if self.on_message:
                self.on_message(
                    {
                        "sender": sender,
                        "room_id": room_id,
                        "body": text.strip(),
                        "mine": True,
                        "ts": datetime.now().strftime("%H:%M"),
                    }
                )

                # echo + admin pseudo commands
                if text.strip().startswith("/admin"):
                    self.on_message(
                        {
                            "sender": "@sandbox-admin:local",
                            "room_id": room_id,
                            "body": "Admin action accepted (sandbox).",
                            "mine": False,
                            "ts": datetime.now().strftime("%H:%M"),
                        }
                    )
                else:
                    self.on_message(
                        {
                            "sender": "@sandbox-echo:local",
                            "room_id": room_id,
                            "body": f"echo: {text.strip()}",
                            "mine": False,
                            "ts": datetime.now().strftime("%H:%M"),
                        }
                    )
            return

        if not self.client or not self._loop:
            self._emit_state("Нет активного подключения к Matrix")
            return

        async def _send():
            await self.client.room_send(
                room_id=room_id,
                message_type="m.room.message",
                content={"msgtype": "m.text", "body": text.strip()},
            )

        asyncio.run_coroutine_threadsafe(_send(), self._loop)


def main(page: ft.Page):
    page.title = APP_NAME
    page.theme_mode = ft.ThemeMode.DARK
    page.bgcolor = BG
    page.padding = 10
    page.spacing = 0
    page.window_min_width = 1024
    page.window_min_height = 700

    svc = MatrixService()
    events_q: "queue.Queue[tuple[str, object]]" = queue.Queue()

    homeserver = ft.TextField(label="Homeserver", value=os.getenv("MATRIX_HOMESERVER", "sandbox"), border_color=BORDER)
    user = ft.TextField(label="Логин", value=os.getenv("MATRIX_USER", "@sandbox-user:local"), border_color=BORDER)
    password = ft.TextField(label="Пароль", value=os.getenv("MATRIX_PASSWORD", "sandbox"), password=True, can_reveal_password=True, border_color=BORDER)

    status_text = ft.Text("Отключено", color=TEXT_MUTED, size=12)

    rooms_dd = ft.Dropdown(label="Комната", options=[], border_color=BORDER, visible=False)
    message_input = ft.TextField(
        hint_text="Напиши сообщение...",
        multiline=True,
        min_lines=1,
        max_lines=4,
        border_color=BORDER,
        bgcolor=SURFACE_2,
        expand=True,
    )
    messages_col = ft.Column(scroll=ft.ScrollMode.AUTO, expand=True, spacing=8)
    chat_list = ft.ListView(expand=True, spacing=4, padding=0)
    selected_room_title = ft.Text("Выбери чат", weight=ft.FontWeight.W_600, size=16)

    all_messages: Dict[str, List[dict]] = {}

    def bubble(msg: dict) -> ft.Control:
        mine = msg.get("mine", False)
        sender = "Вы" if mine else msg["sender"]
        return ft.Row(
            alignment=ft.MainAxisAlignment.END if mine else ft.MainAxisAlignment.START,
            controls=[
                ft.Container(
                    content=ft.Column(
                        spacing=4,
                        controls=[
                            ft.Text(sender, color=TEXT_MUTED, size=11),
                            ft.Text(msg["body"], selectable=True),
                            ft.Text(msg.get("ts", ""), size=10, color=TEXT_MUTED),
                        ],
                    ),
                    padding=8,
                    bgcolor=ACCENT if mine else SURFACE_2,
                    border_radius=10,
                    border=ft.border.all(1, ACCENT if mine else BORDER),
                    width=460,
                )
            ],
        )

    def rerender_messages():
        rid = rooms_dd.value
        messages_col.controls.clear()
        if not rid:
            page.update()
            return
        for msg in all_messages.get(rid, []):
            messages_col.controls.append(bubble(msg))
        selected_room_title.value = next(
            (opt.text for opt in rooms_dd.options if opt.key == rid),
            "Чат",
        )
        page.update()

    def set_status(text: str):
        status_text.value = text

    def close_login_sheet():
        if getattr(page, "close", None):
            page.close(login_sheet)
        else:
            login_sheet.open = False
            page.update()

    def open_login_sheet():
        if getattr(page, "open", None):
            page.open(login_sheet)
        else:
            login_sheet.open = True
            page.update()

    def show_main_window():
        app_content.visible = True
        page.update()

    def handle_connect(_=None):
        svc.connect(homeserver.value or "", user.value or "", password.value or "")
        set_status("Подключение...")
        close_login_sheet()
        show_main_window()

    def handle_disconnect(_):
        svc.stop()
        set_status("Отключено")
        page.update()

    def handle_send(_):
        rid = rooms_dd.value
        if not rid:
            set_status("Выбери комнату")
            page.update()
            return
        text = message_input.value or ""
        if not text.strip():
            return
        svc.send_message(rid, text, user.value or "@me:local")
        message_input.value = ""
        page.update()

    def room_changed(_):
        rebuild_chat_list()
        rerender_messages()

    def select_room(room_id: str):
        rooms_dd.value = room_id
        room_changed(None)
        page.update()

    def room_tile(room: MatrixRoom) -> ft.Control:
        room_messages = all_messages.get(room.room_id, [])
        last_message = room_messages[-1]["body"] if room_messages else "Нет сообщений"
        is_selected = rooms_dd.value == room.room_id
        return ft.Container(
            border_radius=10,
            bgcolor=SURFACE_2 if is_selected else None,
            border=ft.border.all(1, ACCENT if is_selected else BORDER),
            padding=10,
            ink=True,
            on_click=lambda _: select_room(room.room_id),
            content=ft.Column(
                spacing=2,
                controls=[
                    ft.Text(room.display_name, weight=ft.FontWeight.W_600, max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
                    ft.Text(last_message, size=11, color=TEXT_MUTED, max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
                ],
            ),
        )

    def rebuild_chat_list():
        chat_list.controls.clear()
        for room in svc.rooms.values():
            chat_list.controls.append(room_tile(room))

    def poll_events():
        changed = False
        while True:
            try:
                event, payload = events_q.get_nowait()
            except queue.Empty:
                break

            changed = True
            if event == "rooms":
                rooms: List[MatrixRoom] = payload  # type: ignore
                rooms_dd.options = [ft.dropdown.Option(key=r.room_id, text=r.display_name) for r in rooms]
                if not rooms_dd.value and rooms:
                    rooms_dd.value = rooms[0].room_id
                rebuild_chat_list()
            elif event == "msg":
                msg: dict = payload  # type: ignore
                rid = msg["room_id"]
                all_messages.setdefault(rid, []).append(msg)
                rebuild_chat_list()
            elif event == "state":
                set_status(str(payload))

        if changed:
            rerender_messages()

        page.run_task(_poll_task)

    async def _poll_task():
        await asyncio.sleep(0.35)
        poll_events()

    svc.on_rooms = lambda rooms: events_q.put(("rooms", rooms))
    svc.on_message = lambda message: events_q.put(("msg", message))
    svc.on_state = lambda text: events_q.put(("state", text))

    login_sheet = ft.BottomSheet(
        open=False,
        draggable=True,
        show_drag_handle=True,
        bgcolor=SURFACE,
        content=ft.Container(
            padding=20,
            height=370,
            content=ft.Column(
                spacing=12,
                controls=[
                    ft.Text("Вход в Matrix", size=20, weight=ft.FontWeight.W_600),
                    homeserver,
                    user,
                    password,
                    ft.Text("Подсказка: sandbox/demo включают локальную песочницу.", size=12, color=TEXT_MUTED),
                    ft.Row(
                        alignment=ft.MainAxisAlignment.END,
                        controls=[
                            ft.TextButton("Отмена", on_click=lambda _: close_login_sheet()),
                            ft.ElevatedButton("Войти", bgcolor=ACCENT, color="white", on_click=handle_connect),
                        ],
                    ),
                ],
            ),
        ),
    )

    def open_login(_):
        open_login_sheet()

    app_content = ft.Stack(
        expand=True,
        visible=False,
        controls=[
            ft.Row(
                expand=True,
                spacing=10,
                controls=[
                    ft.Container(
                        width=310,
                        bgcolor=SURFACE,
                        border=ft.border.all(1, BORDER),
                        border_radius=12,
                        padding=10,
                        content=ft.Column(
                            expand=True,
                            spacing=8,
                            controls=[
                                ft.Row(
                                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                                    controls=[
                                        ft.Text("BnDChat", size=24, weight=ft.FontWeight.BOLD),
                                        status_text,
                                    ],
                                ),
                                rooms_dd,
                                ft.Divider(height=1, color=BORDER),
                                chat_list,
                            ]
                        ),
                    ),
                    ft.Container(
                        expand=True,
                        bgcolor=CHAT_BG,
                        border=ft.border.all(1, BORDER),
                        border_radius=12,
                        padding=10,
                        content=ft.Column(
                            expand=True,
                            controls=[
                                ft.Row(
                                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                                    controls=[
                                        selected_room_title,
                                        ft.Row(
                                            spacing=0,
                                            controls=[
                                                ft.IconButton(
                                                    icon=ft.Icons.LOGIN_ROUNDED,
                                                    icon_color=TEXT_MUTED,
                                                    on_click=open_login,
                                                    tooltip="Войти",
                                                ),
                                                ft.IconButton(icon=ft.Icons.POWER_SETTINGS_NEW, icon_color=TEXT_MUTED, on_click=handle_disconnect, tooltip="Отключиться"),
                                            ],
                                        ),
                                    ],
                                ),
                                ft.Divider(height=1, color=BORDER),
                                messages_col,
                                ft.Row(
                                    vertical_alignment=ft.CrossAxisAlignment.END,
                                    controls=[
                                        message_input,
                                        ft.IconButton(icon=ft.Icons.SEND_ROUNDED, icon_color=ACCENT, on_click=handle_send),
                                    ]
                                ),
                            ],
                        ),
                    ),
                ],
            ),
        ],
    )
    page.add(app_content)

    rooms_dd.on_change = room_changed
    page.on_keyboard_event = lambda e: handle_send(e) if (e.key == "Enter" and e.shift is False) else None
    open_login_sheet()
    poll_events()


if __name__ == "__main__":
    ft.app(target=main)
