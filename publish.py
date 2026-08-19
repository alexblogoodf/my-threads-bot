import os
import sys
import time
import json
import requests

ACCESS_TOKEN = os.environ.get("THREADS_ACCESS_TOKEN")
USER_ID = os.environ.get("THREADS_USER_ID")
REPO_NAME = os.environ.get("GITHUB_REPOSITORY")
BRANCH = os.environ.get("GITHUB_REF_NAME", "main")
GRAPH_URL = "https://graph.threads.net/v1.0"

def get_raw_media_url(folder_name, filename):
    if not filename:
        return None
    return f"https://raw.githubusercontent.com/{REPO_NAME}/{BRANCH}/posts/{folder_name}/{filename}"

def create_item_container(media_url, is_carousel=False):
    """Создаёт контейнер для одиночного медиа элемента"""
    # 🛡 ЗАЩИТА: Если URL пустой, не пытаемся вызвать .lower(), а просто выходим
    if not media_url:
        return None 
        
    url = f"{GRAPH_URL}/{USER_ID}/threads"
    is_video = media_url.lower().endswith(('.mp4', '.mov'))
    payload = {
        "access_token": ACCESS_TOKEN,
        "media_type": "VIDEO" if is_video else "IMAGE",
        "is_carousel_item": "true" if is_carousel else "false"
    }
    if is_video:
        payload["video_url"] = media_url
    else:
        payload["image_url"] = media_url
    res = requests.post(url, data=payload).json()
    if "id" not in res:
        # Если Meta упала (Code 2), скрипт упадет здесь. Завтра cron попробует снова!
        raise Exception(f"Ошибка создания медиа-контейнера: {res}")
    return res["id"]

def create_main_container(text, topic_tag=None, media_children=None, reply_to_id=None):
    """Создаёт главный контейнер (Текст, Карусель или Ответ)"""
    url = f"{GRAPH_URL}/{USER_ID}/threads"
    payload = {
        "access_token": ACCESS_TOKEN,
        "text": text,
    }
    if topic_tag:
        payload["topic_tag"] = topic_tag
    if reply_to_id:
        payload["reply_to_id"] = reply_to_id
    if media_children:
        if len(media_children) > 1:
            payload["media_type"] = "CAROUSEL"
            payload["children"] = ",".join(media_children)
    else:
        payload["media_type"] = "TEXT"
    res = requests.post(url, data=payload).json()
    if "id" not in res:
        raise Exception(f"Ошибка создания главного контейнера: {res}")
    return res["id"]

def publish_container(creation_id):
    """Публикует подготовленный контейнер"""
    url = f"{GRAPH_URL}/{USER_ID}/threads_publish"
    payload = {
        "access_token": ACCESS_TOKEN,
        "creation_id": creation_id
    }
    res = requests.post(url, data=payload).json()
    if "id" not in res:
        raise Exception(f"Ошибка публикации: {res}")
    return res["id"]

def check_container_status(container_id):
    """Умная проверка статуса видео. Экономит минуты GitHub Actions."""
    url = f"{GRAPH_URL}/{container_id}"
    payload = {
        "access_token": ACCESS_TOKEN,
        "fields": "status,error_message"
    }
    # Meta обычно обрабатывает видео 30-60 секунд. 
    # Спим один раз 45 секунд, чтобы не гонять сервер и не тратить циклы процессора раннера.
    time.sleep(45) 
    max_attempts = 4 # Максимум еще ~2 минуты ожидания
    for attempt in range(max_attempts):
        res = requests.get(url, params=payload).json()
        status = res.get("status")
        if status == "FINISHED":
            return True
        elif status == "ERROR":
            raise Exception(f"Ошибка обработки видео Meta: {res.get('error_message')}")
        # Если еще в процессе, ждем еще 30 секунд перед следующей проверкой
        time.sleep(30)
    raise Exception("Таймаут: Meta не успела обработать видео за 2.5 минуты.")

def main():
    state_file = "state.json"
    if os.path.exists(state_file):
        with open(state_file, "r", encoding="utf-8") as f:
            state = json.load(f)
    else:
        state = {"next_index": 0}

    posts_dir = "posts"
    if not os.path.exists(posts_dir):
        print("Папка posts не найдена!")
        sys.exit(1)

    folders = sorted([f for f in os.listdir(posts_dir) if os.path.isdir(os.path.join(posts_dir, f))])
    if not folders:
        print("Папки с постами не найдены!")
        sys.exit(0)

    current_index = state["next_index"] % len(folders)
    folder_name = folders[current_index]
    post_path = os.path.join(posts_dir, folder_name, "content.json")
    print(f"Публикуем пост из папки: {folder_name} ({current_index + 1}/{len(folders)})")

    with open(post_path, "r", encoding="utf-8") as f:
        post_data = json.load(f)

    text = post_data.get('text', '').strip()
    topic = post_data.get('topic', '').strip()
    media_files = [f for f in post_data.get("media_files", []) if f]
    main_container_id = None

    # --- СЦЕНАРИЙ 1: Карусель ---
    if len(media_files) > 1:
        print(f"Обнаружена карусель из {len(media_files)} файлов...")
        child_ids = []
        for filename in media_files:
            media_url = get_raw_media_url(folder_name, filename)
            
            # 🛡 ЗАЩИТА: Если URL пустой, пропускаем этот файл и идем дальше
            if not media_url:
                print(f"⚠️ Пропущен пустой элемент в карусели")
                continue
                
            child_id = create_item_container(media_url, is_carousel=True)
            
            # Если функция вернула None, не добавляем в список
            if not child_id:
                continue
                
            if media_url.lower().endswith(('.mp4', '.mov')):
                check_container_status(child_id)
            child_ids.append(child_id)
            time.sleep(1) # Минимальная задержка, чтобы не упереться в Rate Limit API
            
        time.sleep(2) 
        url = f"{GRAPH_URL}/{USER_ID}/threads"
        payload = {
            "access_token": ACCESS_TOKEN,
            "media_type": "CAROUSEL",
            "children": ",".join(child_ids),
            "text": text
        }
        if topic:
            payload["topic_tag"] = topic
        res = requests.post(url, data=payload).json()
        if "id" not in res:
            raise Exception(f"Ошибка создания карусели: {res}")
        main_container_id = res["id"]

    # --- СЦЕНАРИЙ 2: Одиночное медиа ---
    elif len(media_files) == 1:
        print("Обнаружен 1 медиафайл...")
        media_url = get_raw_media_url(folder_name, media_files[0])
        is_video = media_url.lower().endswith(('.mp4', '.mov'))
        url = f"{GRAPH_URL}/{USER_ID}/threads"
        payload = {
            "access_token": ACCESS_TOKEN,
            "text": text,
            "media_type": "VIDEO" if is_video else "IMAGE"
        }
        if is_video:
            payload["video_url"] = media_url
        else:
            payload["image_url"] = media_url
        if topic:
            payload["topic_tag"] = topic
        res = requests.post(url, data=payload).json()
        if "id" not in res:
            raise Exception(f"Ошибка создания медиа-поста: {res}")
        main_container_id = res["id"]
        if is_video:
            check_container_status(main_container_id)

    # --- СЦЕНАРИЙ 3: Только текст ---
    else:
        print("Текстовый пост без медиа...")
        main_container_id = create_main_container(
            text=text,
            topic_tag=topic if topic else None
        )

    # Публикация основного поста
    time.sleep(2)
    published_main_id = publish_container(main_container_id)
    print(f"Основной пост опубликован! ID: {published_main_id}")

    # Публикация ответа (Reply)
    if post_data.get("reply_text"):
        time.sleep(2)
        print("Публикуем ответ (Reply)...")
        reply_container_id = create_main_container(
            text=post_data["reply_text"],
            topic_tag=topic if topic else None,
            reply_to_id=published_main_id
        )
        time.sleep(2)
        published_reply_id = publish_container(reply_container_id)
        print(f"Ответ опубликован! ID: {published_reply_id}")

    # Обновляем состояние ТОЛЬКО если всё прошло успешно
    state["next_index"] = (current_index + 1) % len(folders)
    with open(state_file, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)

if __name__ == "__main__":
    main()
