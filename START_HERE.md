# Video Auditor — шпаргалка по запуску (Windows)

Папка проекта:
```powershell
C:\Users\Даниил\Desktop\video_auditor_project
```

## 1. Первый запуск или запуск после перезагрузки компьютера

### Шаг 1. Открой Docker Desktop

Дождись, пока Docker Desktop сообщит, что движок запущен.

### Шаг 2. Запусти PostgreSQL

Открой первое окно PowerShell и выполни:

```powershell
cd "C:\Users\Даниил\Desktop\video_auditor_project"
docker compose up -d postgres
docker compose ps
```

У контейнера `video_auditor_postgres` статус должен быть `Up` или `healthy`. В проекте внешний порт PostgreSQL — **5433** (внутри контейнера — 5432).

### Шаг 3. Проверь настройки (только если это первый запуск)

Файл `.env` должен находиться в корне проекта. Если его ещё нет, создай его из примера:

```powershell
Copy-Item .env.example .env
notepad .env
```

В `.env` нужно указать собственный `GEMINI_API_KEY` и проверить строку подключения:

```dotenv
DATABASE_URL=postgresql+psycopg://video_auditor:video_auditor_dev_password@localhost:5433/video_auditor
GEMINI_API_KEY=твой_ключ_Gemini
```

**Если файл `.env` уже существует, не выполняй `Copy-Item` повторно:** это может заменить твои настройки. Не публикуй `.env` в GitHub.

### Шаг 4. Установи зависимости и выполни миграции (первый запуск или после изменения зависимостей/миграций)

В PowerShell с активированным виртуальным окружением:

```powershell
cd "C:\Users\Даниил\Desktop\video_auditor_project"
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
alembic upgrade head
```

Если виртуального окружения ещё нет, создай его один раз:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

## 2. Обычный запуск проекта — три окна PowerShell

PostgreSQL должен быть запущен (шаг 2 выше).

### Окно № 1 — база данных

Если контейнер PostgreSQL ещё не запущен:

```powershell
cd "C:\Users\Даниил\Desktop\video_auditor_project"
docker compose up -d postgres
```

Это можно выполнить повторно: уже работающий контейнер останется запущенным.

### Окно № 2 — веб-сервер FastAPI

```powershell
cd "C:\Users\Даниил\Desktop\video_auditor_project"
.\.venv\Scripts\Activate.ps1
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Оставь окно открытым, пока пользуешься приложением.

### Окно № 3 — worker, который анализирует видео

```powershell
cd "C:\Users\Даниил\Desktop\video_auditor_project"
.\.venv\Scripts\Activate.ps1
python -m app.worker
```

Это окно тоже должно оставаться открытым. Без worker новые задания будут оставаться в статусе `pending`, а при долгом анализе смотри сообщения именно в этом окне.

## 3. Какие адреса открыть в браузере

- **Основной интерфейс:** http://127.0.0.1:8000/
- **Swagger / документация API:** http://127.0.0.1:8000/docs
- **Проверка доступности API:** http://127.0.0.1:8000/health

В интерфейсе вставь публичную ссылку YouTube, выбери тип контента и нажми «Запустить проверку». Статус и готовый отчёт показываются на той же странице.

## 4. Как остановить проект

- В окне API нажми `Ctrl + C`.
- В окне worker нажми `Ctrl + C`.
- Чтобы остановить PostgreSQL, выполни в любом PowerShell из папки проекта:

```powershell
docker compose stop postgres
```

**Не используй `docker compose down -v`, если хочешь сохранить историю аудитов:** параметр `-v` удаляет том с данными базы.

## 5. Как получить последние изменения с GitHub

Перед обновлением посмотри, нет ли своих локальных изменений:

```powershell
cd "C:\Users\Даниил\Desktop\video_auditor_project"
git status
git pull --ff-only origin main
```

Если менялись зависимости, после обновления выполни `python -m pip install -r requirements.txt`. Если добавились новые миграции, выполни `alembic upgrade head`. После обновления перезапусти API и worker; вкладку интерфейса обнови в браузере.

Если `git pull` сообщает о локальных изменениях или конфликте, не делай сброс и не удаляй файлы вслепую — сначала проверь вывод `git status`.

## 6. Быстрая диагностика

**Интерфейс не открывается**
- Убедись, что окно API не закрыто и в нём нет traceback.
- Открой http://127.0.0.1:8000/ заново.

**Задание долго в статусе `processing`**
- Проверь окно worker: найди сообщения про нужный ID и ошибки Gemini.
- Убедись, что `GEMINI_API_KEY` заполнен в `.env`.
- Не отправляй ту же ссылку повторно, пока не проверил существующее задание.

**Ошибка подключения к PostgreSQL**
- Выполни `docker compose ps`.
- Проверь, что контейнер PostgreSQL работает.
- В локальном `.env` строка `DATABASE_URL` должна использовать `localhost:5433`, а внутри Docker Compose сервисы подключаются к `postgres:5432`.

**PowerShell не даёт активировать виртуальное окружение**
В том же окне выполни временное разрешение для текущего процесса, а затем повтори активацию:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
```

## Важно

Аудит — предварительная оценка моделью, а не гарантия обнаружения всех дефектов. Проверяй спорные результаты вручную. Не отправляй в Gemini материалы, на передачу которых у тебя нет разрешения.
