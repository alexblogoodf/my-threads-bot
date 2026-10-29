import os
import sys
import time
import json
import requests

# ───────────────────────────────────────────────
# Конфигурация
# ───────────────────────────────────────────────
BUFFER_API = "https://api.buffer.com"
BUFFER_TOKEN = os.environ.get("BUFFER_PPSTORE_BLUESKY", "")
REPO_NAME = os.environ.get("GITHUB_REPOSITORY", "")
BRANCH = os.environ.get("GITHUB_REF_NAME", "main")

STATE_FILE = "state_bluesky.json"
POSTS_DIR = "posts"
MAX_POST_LENGTH = 300  # Лимит символов для Bluesky

# Хештеги по темам
HASHTAGS = {
    "wallpaper": (
        "#wallpaper #wallpapers #iphone"
    ),
    "glyfiq": (
        "#figma #framer #icondesign"
    ),
    "rotime": (
        "#anbernic #console #retrogaming"
    ),
}


# ───────────────────────────────────────────────
# Buffer GraphQL API
# ───────────────────────────────────────────────
def buffer_graphql(query):
    """Отправляет запрос к Buffer GraphQL API."""
    r = requests.post(
        BUFFER_API,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {BUFFER_TOKEN}"
        },
        json={"query": query},
        timeout=30
    )
    r.raise_for_status()
    data = r.json()
    if data.get("errors"):
        raise Exception(f"GraphQL error: {data['errors']}")
    return data["data"]


def get_bluesky_channel_id():
    """Автоматически находит Bluesky-канал в аккаунте Buffer."""
    data = buffer_graphql("query { account { organizations { id name } } }")
    orgs = data["account"]["organizations"]
    if not orgs:
        raise Exception("В аккаунте Buffer нет организаций")

    org_id = orgs[0]["id"]
    print(f"Организация: {orgs[0]['name']} (id: {org_id})")

    data = buffer_graphql(
        'query { channels(input: { organizationId: "%s" }) { id name service } }' % org_id
    )
    channels = data.get("channels", [])
    
    # Ищем Bluesky по service
    bluesky_channels = [ch for ch in channels if ch.get("service") == "bluesky"]
    
    # Если не нашли по service, ищем по имени
    if not bluesky_channels:
        bluesky_channels = [ch for ch in channels if "bluesky" in ch.get("name", "").lower()]
    
    if not bluesky_channels:
        print(f"⚠️ Bluesky-канал не найден. Доступные каналы:")
        for ch in channels:
            print(f"  - id={ch.get('id')} service={ch.get('service')} name={ch.get('name')}")
        raise Exception("К Buffer не подключен Bluesky-канал")

    channel = bluesky_channels[0]
    print(f"🦋 Найден Bluesky-канал: {channel['name']} (id: {channel['id']})")
    return channel["id"]


def buffer_create_image_post(channel_id, text, image_url):
    """Публикует пост с изображением через Buffer GraphQL."""
    text_lit = json.dumps(text, ensure_ascii=False)
    ch_lit = json.dumps(channel_id)
    url_lit = json.dumps(image_url)

    query = f'''mutation {{
      createPost(input: {{
        text: {text_lit},
        channelId: {ch_lit},
        schedulingType: automatic,
        mode: shareNow,
        assets: [{{ image: {{ url: {url_lit} }} }}]
      }}) {{
        ... on PostActionSuccess {{ post {{ id text }} }}
        ... on MutationError {{ message }}
      }}
    }}'''

    data = buffer_graphql(query)
    res = data.get("createPost", {})

    if res.get("post"):
        return True, res["post"].get("id")
    return False, res.get("message", "неизвестная ошибка Buffer")


# ───────────────────────────────────────────────
# Вспомогательные функции
# ───────────────────────────────────────────────
def get_media_url(folder_name, filename):
    """Прямая ссылка на файл через raw.githubusercontent."""
    return f"https://raw.githubusercontent.com/{REPO_NAME}/{BRANCH}/posts/{folder_name}/{filename}"


def folder_has_video(folder_path):
    """Проверяет, есть ли в папке видеофайл."""
    for fname in os.listdir(folder_path):
        if fname.lower().endswith((".mp4", ".mov", ".avi")):
            return True
    return False


def get_first_image(folder_path):
    """Находит первое изображение в папке (01.jpg, 01.png и т.д.)."""
    files = sorted(os.listdir(folder_path))
    
    # Приоритет: 01.jpg, 01.png
    for ext in [".jpg", ".jpeg", ".png", ".webp"]:
        fname = f"01{ext}"
        if fname in files:
            return fname
    
    # Иначе первое попавшееся изображение
    for fname in files:
        if fname.lower().endswith((".jpg", ".jpeg", ".png", ".webp")):
            return fname
    
    return None


def detect_topic(post_data, folder_name):
    """Определяет тему поста для выбора хештегов."""
    topic = (post_data.get("topic") or "").strip().lower()
    text = (post_data.get("text") or "").lower()
    reply = (post_data.get("reply_text") or "").lower()
    
    # Проверяем по полю topic
    if topic in HASHTAGS:
        return topic
    
    # Проверяем по содержимому текста
    full_text = text + " " + reply
    if "glyfiq" in full_text or "figma" in full_text or "icon" in full_text:
        return "glyfiq"
    if "rotime" in full_text or "anbernic" in full_text or "rg rotate" in full_text:
        return "rotime"
    if "wallpaper" in full_text or "iphone" in full_text:
        return "wallpaper"
    
    return None


def build_post_text(post_data, topic):
    """
    Формирует текст поста:
    - text + reply_text (объединены)
    - обрезка до 300 символов с учётом хештегов
    - добавление хештегов по теме
    """
    text = (post_data.get("text") or "").strip()
    reply = (post_data.get("reply_text") or "").strip()
    
    # Объединяем текст
    if text and reply:
        combined = f"{text}\n{reply}"
    elif text:
        combined = text
    elif reply:
        combined = reply
    else:
        combined = ""
    
    # Получаем хештеги для темы
    hashtags = HASHTAGS.get(topic, "")
    
    if hashtags:
        # Считаем сколько места留给 основной текст
        # Формат: основной текст + \n\n + хештеги
        separator = "\n\n"
        max_text_length = MAX_POST_LENGTH - len(hashtags) - len(separator)
        
        if len(combined) > max_text_length:
            # Обрезаем и добавляем многоточие
            combined = combined[:max_text_length - 3].rstrip() + "..."
        
        final_text = f"{combined}{separator}{hashtags}"
    else:
        # Без хештегов, просто обрезаем до 300
        if len(combined) > MAX_POST_LENGTH:
            combined = combined[:MAX_POST_LENGTH - 3].rstrip() + "..."
        final_text = combined
    
    return final_text


# ───────────────────────────────────────────────
# Состояние (по кругу)
# ───────────────────────────────────────────────
def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"next_index": 0}


def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)


# ───────────────────────────────────────────────
# Основная логика
# ───────────────────────────────────────────────
def main():
    if not BUFFER_TOKEN:
        print("❌ Токен BUFFER_PPSTORE_BLUESKY не задан!")
        sys.exit(1)

    if not REPO_NAME:
        print("❌ GITHUB_REPOSITORY не задана (запуск вне GitHub Actions).")
        sys.exit(1)

    # 1) Находим Bluesky-канал
    channel_id = get_bluesky_channel_id()

    # 2) Читаем состояние и список папок
    state = load_state()

    if not os.path.exists(POSTS_DIR):
        print("Папка posts не найдена!")
        sys.exit(1)

    folders = sorted([
        f for f in os.listdir(POSTS_DIR)
        if os.path.isdir(os.path.join(POSTS_DIR, f))
    ])
    if not folders:
        print("Папки с постами не найдены!")
        sys.exit(0)

    total = len(folders)
    current_index = state["next_index"] % total
    folder_name = folders[current_index]
    folder_path = os.path.join(POSTS_DIR, folder_name)
    post_path = os.path.join(folder_path, "content.json")

    print(f"📂 Папка: {folder_name} ({current_index + 1}/{total})")

    # 3) Если есть видео → пропускаем
    if folder_has_video(folder_path):
        print(f"⏭ В папке {folder_name} есть видео — пропускаем.")
        state["next_index"] = (current_index + 1) % total
        save_state(state)
        sys.exit(0)

    # 4) Читаем content.json
    with open(post_path, "r", encoding="utf-8") as f:
        post_data = json.load(f)

    # 5) Находим первое изображение
    image_name = get_first_image(folder_path)
    if not image_name:
        print(f"⚠️ Изображение не найдено в папке {folder_name} — пропускаем.")
        state["next_index"] = (current_index + 1) % total
        save_state(state)
        sys.exit(0)

    image_url = get_media_url(folder_name, image_name)
    print(f" Изображение: {image_name} → {image_url}")

    # 6) Определяем тему и формируем текст
    topic = detect_topic(post_data, folder_name)
    if topic:
        print(f"🏷 Тема: {topic}")
    else:
        print(f"🏷 Тема не определена (без хештегов)")

    post_text = build_post_text(post_data, topic)
    print(f"📝 Текст поста ({len(post_text)} символов):\n{post_text}\n")

    # Проверка длины
    if len(post_text) > MAX_POST_LENGTH:
        print(f"⚠️ Текст превышает {MAX_POST_LENGTH} символов! Обрезаем...")
        post_text = post_text[:MAX_POST_LENGTH - 3].rstrip() + "..."

    # 7) Публикуем
    try:
        ok, info = buffer_create_image_post(channel_id, post_text, image_url)
    except Exception as e:
        ok, info = False, str(e)

    # Проверяем, не дубликат ли это
    already_posted = "already got this one scheduled" in str(info) or "same thing twice" in str(info)

    if ok or already_posted:
        if already_posted:
            print(f"⚠️ Buffer сообщает, что пост уже опубликован/запланирован. Считаем успехом.")
        else:
            print(f"✅ Пост опубликован в Bluesky! ID: {info}")
    else:
        print(f"❌ Ошибка публикации: {info}")
        sys.exit(1)

    # 8) Обновляем состояние
    state["next_index"] = (current_index + 1) % total
    save_state(state)
    print("💾 Состояние сохранено. Готово!")


if __name__ == "__main__":
    main()
