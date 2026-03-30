# BnDChat-Flet

Порт проекта **BnDChat (PyQt5)** на **Flet** с сохранением функционала Matrix-клиента и цветовой схемы (чёрный + оранжевый акцент), но с более современным UI.

## Что реализовано

- Подключение к Matrix homeserver по логину/паролю (`matrix-nio`).
- Загрузка списка joined-комнат.
- Отправка текстовых сообщений (`m.room.message`, `m.text`).
- Получение сообщений в реальном времени через sync-loop.
- Sandbox/demo режим для локального теста без Synapse.
- Demo echo-бот и sandbox admin-команда (`/admin`).

## Запуск

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -r requirements.txt
python app.py
```

### Быстрый sandbox-режим

Оставьте значения по умолчанию:

- Homeserver: `sandbox`
- Логин: `@sandbox-user:local`
- Пароль: `sandbox`

## Matrix-режим

Перед запуском можно задать env:

```bash
export MATRIX_HOMESERVER="https://your-synapse.example.com"
export MATRIX_USER="@alice:your-synapse.example.com"
export MATRIX_PASSWORD="your-password"
python app.py
```

## Ограничения MVP

- Только текстовые сообщения.
- Нет создания комнаты/регистрации из UI.
- Нет обработки медиа и E2EE-истории в интерфейсе.
