import os
import sys
import json
import glob

# Словарь замен: что ищем → на что меняем
REPLACEMENTS = {
    "Buy it here": "Get yours today",
    "Try it free": "See it in action",
}

POSTS_DIR = "posts"


def replace_in_string(text):
    """Применяет все замены к строке. Возвращает (новый_текст, было_ли_изменение)."""
    changed = False
    for old, new in REPLACEMENTS.items():
        if old in text:
            text = text.replace(old, new)
            changed = True
    return text, changed


def replace_in_value(value):
    """Рекурсивно проходит по JSON и меняет строки."""
    if isinstance(value, str):
        return replace_in_string(value)
    elif isinstance(value, list):
        changed_any = False
        new_list = []
        for item in value:
            new_item, changed = replace_in_value(item)
            new_list.append(new_item)
            if changed:
                changed_any = True
        return new_list, changed_any
    elif isinstance(value, dict):
        changed_any = False
        new_dict = {}
        for k, v in value.items():
            new_v, changed = replace_in_value(v)
            new_dict[k] = new_v
            if changed:
                changed_any = True
        return new_dict, changed_any
    else:
        return value, False


def process_file(filepath):
    """Обрабатывает один content.json. Возвращает True, если файл был изменён."""
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        print(f"  ⚠️ Не удалось прочитать {filepath}: {e}")
        return False

    new_data, changed = replace_in_value(data)

    if not changed:
        return False

    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(new_data, f, ensure_ascii=False, indent=2)

    return True


def main():
    if not os.path.exists(POSTS_DIR):
        print(f"❌ Папка '{POSTS_DIR}' не найдена в текущей директории.")
        sys.exit(1)

    # Ищем все content.json в подпапках posts/
    pattern = os.path.join(POSTS_DIR, "*", "content.json")
    files = sorted(glob.glob(pattern))

    if not files:
        print("🔍 Файлы content.json не найдены.")
        sys.exit(0)

    print(f"🔍 Найдено файлов content.json: {len(files)}")
    print(f"🔄 Применяем замены:")
    for old, new in REPLACEMENTS.items():
        print(f"     '{old}' → '{new}'")
    print()

    changed_files = []
    unchanged_files = []

    for filepath in files:
        folder = os.path.basename(os.path.dirname(filepath))
        if process_file(filepath):
            print(f"  ✅ {folder}/content.json — изменён")
            changed_files.append(filepath)
        else:
            unchanged_files.append(filepath)

    print()
    print(f"📊 Итого:")
    print(f"   Изменено файлов: {len(changed_files)}")
    print(f"   Без изменений:   {len(unchanged_files)}")

    if changed_files:
        print()
        print("📝 Изменённые файлы:")
        for f in changed_files:
            print(f"   - {f}")

    # Возвращаем код 0 если были изменения (чтобы workflow знал, что делать коммит)
    # и код 1 если ничего не менялось (опционально)
    if not changed_files:
        print("\n👌 Все файлы уже содержат актуальные фразы. Менять нечего.")
        sys.exit(0)
    else:
        print(f"\n✅ Готово! Закоммитьте изменения в репозиторий.")
        sys.exit(0)


if __name__ == "__main__":
    main()
