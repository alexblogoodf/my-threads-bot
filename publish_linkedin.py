import os
import sys
import time
import json
import requests

# ───────────────────────────────────────────────
# Конфигурация
# ───────────────────────────────────────────────
BUFFER_API = "https://api.buffer.com"
BUFFER_TOKEN = os.environ.get("LINKEDIN_ACCESS_TOKEN") or os.environ.get("BUFFER_API_KEY", "")
REPO_NAME = os.environ.get("GITHUB_REPOSITORY", "")
BRANCH = os.environ.get("GITHUB_REF_NAME", "main")

STATE_FILE = "state_linkedin.json"
POSTS_DIR = "posts"

WALLPAPER_HASHTAGS = (
    "#Wallpaper #Wallpapers #IPhoneWallpaper #MobileWallpaper #Minimalism "
    "#Design #DigitalArt #Apple #IPhone #TechSetup #Productivity #VisualDesign"
)


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


def get_linkedin_channel_id():
    """Автоматически находит LinkedIn-канал в аккаунте Buffer."""
    data = buffer_graphql("query { account { organizations { id name } } }")
    orgs = data["account"]["organizations"]
    if not orgs:
        raise Exception("В аккаунте Buffer нет организаций")

    org_id = orgs[0]["id"]
    print(f"Организация: {orgs[0]['name']} (id: {org_id})")

    data = buffer_graphql(
        'query { channels(input: { organizationId: "%s" }) { id name service } }' % org_id
    )
    channels = [ch for ch in data.get("channels", []) if ch.get("service") == "linkedin"]
    if not channels:
        raise Exception("К Buffer не подключен LinkedIn-канал")

    print(f"💼 Найден LinkedIn-канал: {channels[0]['name']} (id: {channels[0]['id']})")
    return channels[0]["id"]


def buffer_create_document_post(channel_id, text, pdf_url, thumbnail_url, document_title="wallpaper"):
    """Публикует пост с PDF-документом через Buffer GraphQL."""
    text_lit = json.dumps(text, ensure_ascii=False)
    ch_lit = json.dumps(channel_id)
    url_lit = json.dumps(pdf_url)
    title_lit = json.dumps(document_title, ensure_ascii=False)
    thumb_lit = json.dumps(thumbnail_url)

    query = f'''mutation {{
      createPost(input: {{
        text: {text_lit},
        channelId: {ch_lit},
        schedulingType: automatic,
        mode: shareNow,
        assets: [{{ document: {{ url: {url_lit}, title: {title_lit}, thumbnailUrl: {thumb_lit} }} }}]
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
    """Проверяет, есть ли в папке видеофайл (01.mp4)."""
    for fname in os.listdir(folder_path):
        if fname.lower().endswith(".mp4"):
            return True
    return False


def find_pdf_file(folder_path):
    """Ищем 01.pdf (или любой .pdf) в папке."""
    files = sorted(os.listdir(folder_path))
    if "01.pdf" in files:
        return "01.pdf"
    for fname in files:
        if fname.lower().endswith(".pdf"):
            return fname
    return None


def find_thumbnail(folder_path):
    """Ищем картинку-превью для PDF (01.jpg или первую попавшуюся .jpg/.png)."""
    files = sorted(os.listdir(folder_path))
    if "01.jpg" in files:
        return "01.jpg"
    if "01.png" in files:
        return "01.png"
    for fname in files:
        if fname.lower().endswith((".jpg", ".jpeg", ".png")):
            return fname
    return None


def build_post_text(post_data):
    """
    Формирует текст поста:
    - text
    - reply_text (на следующей строке, без отдельного ответа)
    - хештеги если topic == "wallpaper"
    """
    text = (post_data.get("text") or "").strip()
    reply = (post_data.get("reply_text") or "").strip()
    topic = (post_data.get("topic") or "").strip().lower()

    parts = []
    if text:
        parts.append(text)
    if reply:
        parts.append(reply)

    post_text = "\n".join(parts)

    if topic == "wallpaper":
        if post_text:
            post_text += "\n\n" + WALLPAPER_HASHTAGS
        else:
            post_text = WALLPAPER_HASHTAGS

    return post_text


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
        print("❌ Токен не задан! Добавьте LINKEDIN_ACCESS_TOKEN или BUFFER_API_KEY.")
        sys.exit(1)

    if not REPO_NAME:
        print("❌ GITHUB_REPOSITORY не задана (запуск вне GitHub Actions).")
        sys.exit(1)

    # 1) Находим LinkedIn-канал
    channel_id = get_linkedin_channel_id()

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

    # 5) Ищем PDF
    pdf_name = find_pdf_file(folder_path)
    if not pdf_name:
        print(f"⚠️ PDF не найден в папке {folder_name} — пропускаем.")
        state["next_index"] = (current_index + 1) % total
        save_state(state)
        sys.exit(0)

    # 6) Ищем картинку-превью (thumbnail)
    thumb_name = find_thumbnail(folder_path)
    if not thumb_name:
        print(f"⚠️ Thumbnail (картинка) не найден в папке {folder_name} — пропускаем.")
        state["next_index"] = (current_index + 1) % total
        save_state(state)
        sys.exit(0)

    pdf_url = get_media_url(folder_name, pdf_name)
    thumb_url = get_media_url(folder_name, thumb_name)
    print(f"📄 PDF: {pdf_name} → {pdf_url}")
    print(f"🖼 Thumbnail: {thumb_name} → {thumb_url}")

    # 7) Формируем текст
    post_text = build_post_text(post_data)
    print(f"📝 Текст поста:\n{post_text}\n")

    # 8) Публикуем
    try:
        ok, info = buffer_create_document_post(
            channel_id, post_text, pdf_url, thumb_url, document_title="wallpaper"
        )
    except Exception as e:
        ok, info = False, str(e)

    # Проверяем, не дубликат ли это (пост уже был отправлен ранее)
    already_posted = "already got this one scheduled" in str(info) or "same thing twice" in str(info)

    if ok or already_posted:
        if already_posted:
            print(f"⚠️ Buffer сообщает, что пост уже опубликован/запланирован. Считаем успехом.")
        else:
            print(f"✅ Пост опубликован в LinkedIn! ID: {info}")
    else:
        print(f"❌ Ошибка публикации: {info}")
        sys.exit(1)

    # 9) Обновляем состояние
    state["next_index"] = (current_index + 1) % total
    save_state(state)
    print("💾 Состояние сохранено. Готово!")


if __name__ == "__main__":
    main()
