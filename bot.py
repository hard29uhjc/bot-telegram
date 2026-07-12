import io
import logging
import os
import time
from pathlib import Path
from typing import Optional

from PIL import Image, ImageFilter, ImageOps
from telebot import TeleBot
from telebot.apihelper import ApiTelegramException
from telebot.types import InlineKeyboardButton, InlineKeyboardMarkup

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Простой запуск бота: вставь свой токен сюда.
TOKEN = '8668748411:AAE4yF9SLwT3hIvxQn0nI5qQB30_cUQYOmM'
bot = TeleBot(TOKEN, parse_mode='HTML')
user_data: dict[int, dict[str, object]] = {}

COOLDOWN_SECONDS = 2.0
MAX_FILE_SIZE_MB = 10
MIN_IMAGE_SIZE = (100, 100)
MAX_IMAGE_SIZE = (4000, 4000)
SUPPORTED_EXTENSIONS = {'.jpg', '.jpeg', '.png'}


def get_temp_path(chat_id: int, suffix: str) -> str:
    temp_dir = Path('temp')
    temp_dir.mkdir(exist_ok=True)
    return str(temp_dir / f'{chat_id}_{suffix}')


def build_keyboard(chat_id: int) -> InlineKeyboardMarkup:
    state = user_data.get(chat_id, {})
    mode = state.get('mode', 'standard')
    mode_label = '🤖 AI-режим: ВКЛ' if mode == 'ai' else '🧩 AI-режим: ВЫКЛ'
    keyboard = InlineKeyboardMarkup(row_width=2)
    keyboard.add(
        InlineKeyboardButton(mode_label, callback_data='toggle_mode'),
        InlineKeyboardButton('🔄 Сброс', callback_data='reset'),
        InlineKeyboardButton('🆘 Помощь', callback_data='help'),
    )
    return keyboard


def validate_image_bytes(data: bytes) -> Image.Image:
    if len(data) > MAX_FILE_SIZE_MB * 1024 * 1024:
        raise ValueError(f'Файл слишком большой. Максимум {MAX_FILE_SIZE_MB} МБ.')

    try:
        image = Image.open(io.BytesIO(data))
        image.load()
    except Exception as exc:
        raise ValueError('Не удалось прочитать изображение. Попробуй другой файл.') from exc

    image = ImageOps.exif_transpose(image)
    width, height = image.size
    if width < MIN_IMAGE_SIZE[0] or height < MIN_IMAGE_SIZE[1]:
        raise ValueError('Фото слишком маленькое. Используй изображение хотя бы 100x100 пикселей.')
    if width > MAX_IMAGE_SIZE[0] or height > MAX_IMAGE_SIZE[1]:
        raise ValueError('Фото слишком большое. Уменьши разрешение.')

    if image.mode not in {'RGB', 'RGBA', 'L'}:
        image = image.convert('RGBA')
    return image


def save_photo(file_id: str, path: str) -> None:
    if bot is None:
        raise RuntimeError('Бот не инициализирован. Установите TELEGRAM_TOKEN, TG_BOT_TOKEN или запустите с --token.')

    file_info = bot.get_file(file_id)
    data = bot.download_file(file_info.file_path)
    validate_image_bytes(data)

    with open(path, 'wb') as file:
        file.write(data)


def remove_white_background(image: Image.Image) -> Image.Image:
    image = ImageOps.exif_transpose(image).convert('RGBA')
    pixels = []
    for r, g, b, a in image.getdata():
        if r > 240 and g > 240 and b > 240:
            pixels.append((255, 255, 255, 0))
        else:
            pixels.append((r, g, b, a))
    image.putdata(pixels)
    return image


def apply_tattoo(body_path: str, tattoo_path: str, output_path: str, use_ai: bool = False) -> None:
    body = ImageOps.exif_transpose(Image.open(body_path)).convert('RGBA')
    tattoo = remove_white_background(Image.open(tattoo_path))

    max_width = max(1, body.width // 2)
    if tattoo.width > max_width:
        height = max(1, int(tattoo.height * max_width / tattoo.width))
        tattoo = tattoo.resize((max_width, height), Image.Resampling.LANCZOS)

    position = ((body.width - tattoo.width) // 2, (body.height - tattoo.height) // 2)
    result = Image.new('RGBA', body.size, (255, 255, 255, 255))
    result.paste(body, (0, 0))

    if use_ai:
        tattoo = tattoo.filter(ImageFilter.GaussianBlur(radius=0.7))
        tattoo = tattoo.filter(ImageFilter.UnsharpMask(radius=1, percent=140, threshold=3))

        shadow = Image.new('RGBA', tattoo.size, (0, 0, 0, 90))
        shadow = shadow.filter(ImageFilter.GaussianBlur(radius=3))
        result.paste(shadow, (position[0] + 6, position[1] + 8), shadow)

    result.paste(tattoo, position, tattoo)
    output_dir = Path(output_path).parent
    output_dir.mkdir(exist_ok=True, parents=True)
    result.convert('RGB').save(output_path, 'JPEG', quality=95)


def cleanup_chat_files(chat_id: int, body_path: Optional[str] = None, tattoo_path: Optional[str] = None, result_path: Optional[str] = None) -> None:
    for path in [body_path, tattoo_path, result_path]:
        if path and os.path.exists(path):
            os.remove(path)
    user_data.pop(chat_id, None)


def ensure_state(chat_id: int) -> dict[str, object]:
    state = user_data.get(chat_id)
    if state is None:
        state = {'mode': 'standard'}
        user_data[chat_id] = state
    return state


def is_spam(chat_id: int) -> bool:
    state = ensure_state(chat_id)
    now = time.time()
    last_action = state.get('last_action', 0.0)
    if now - float(last_action) < COOLDOWN_SECONDS:
        return True
    state['last_action'] = now
    return False


def process_images(body_path: str, tattoo_path: str, output_path: str, use_ai: bool = False) -> str:
    output_file = str(output_path)
    apply_tattoo(body_path, tattoo_path, output_file, use_ai=use_ai)
    return output_file


def run_offline_mode(args: argparse.Namespace) -> str:
    base_dir = Path(__file__).resolve().parent
    body_path = Path(args.body or 'offline/body.jpg')
    tattoo_path = Path(args.tattoo or 'offline/tattoo.png')
    output_path = Path(args.output or 'offline/result.jpg')

    if not body_path.is_absolute():
        body_path = base_dir / body_path
    if not tattoo_path.is_absolute():
        tattoo_path = base_dir / tattoo_path
    if not output_path.is_absolute():
        output_path = base_dir / output_path

    if not body_path.exists():
        raise FileNotFoundError(f'Не найден файл тела: {body_path}')
    if not tattoo_path.exists():
        raise FileNotFoundError(f'Не найден файл эскиза: {tattoo_path}')

    result_path = process_images(str(body_path), str(tattoo_path), str(output_path), use_ai=args.ai)
    logger.info('Оффлайн-обработка завершена: %s', result_path)
    print(f'Оффлайн-обработка завершена. Результат: {result_path}')
    return result_path


if bot is not None:
    @bot.message_handler(commands=['start', 'help'])
    def send_welcome(message):
        chat_id = message.chat.id
        state = ensure_state(chat_id)
        state['mode'] = state.get('mode', 'standard')
        bot.send_message(
            chat_id,
            'Привет! Я помогу визуально примерить тату на фото.\n\n'
            'Шаги:\n1. Отправь фото тела\n2. Отправь эскиз тату\n3. Получи результат\n\n'
            'Также можно использовать кнопки ниже.',
            reply_markup=build_keyboard(chat_id),
        )

    @bot.message_handler(commands=['reset'])
    def reset_state(message):
        chat_id = message.chat.id
        user_data.pop(chat_id, None)
        bot.send_message(chat_id, 'Состояние сброшено. Можешь начать заново.', reply_markup=build_keyboard(chat_id))

    @bot.callback_query_handler(func=lambda call: True)
    def handle_callback(call):
        chat_id = call.message.chat.id
        data = call.data or ''

        if data == 'toggle_mode':
            state = ensure_state(chat_id)
            state['mode'] = 'ai' if state.get('mode') != 'ai' else 'standard'
            bot.answer_callback_query(call.id, text='Режим обновлён')
            bot.edit_message_reply_markup(chat_id, call.message.message_id, reply_markup=build_keyboard(chat_id))
            bot.send_message(chat_id, 'Режим обновлён. Отправляй фото и эскиз.', reply_markup=build_keyboard(chat_id))
            return

        if data == 'reset':
            user_data.pop(chat_id, None)
            bot.answer_callback_query(call.id, text='Сброшено')
            bot.edit_message_reply_markup(chat_id, call.message.message_id, reply_markup=build_keyboard(chat_id))
            bot.send_message(chat_id, 'Состояние сброшено. Можно начать заново.', reply_markup=build_keyboard(chat_id))
            return

        if data == 'help':
            bot.answer_callback_query(call.id, text='Помощь')
            bot.send_message(chat_id, 'Отправь фото тела, затем эскиз тату. Используй кнопку AI для более мягкого наложения.', reply_markup=build_keyboard(chat_id))

    @bot.message_handler(content_types=['photo'])
    def handle_photo(message):
        chat_id = message.chat.id
        if is_spam(chat_id):
            bot.reply_to(message, 'Слишком быстро. Подожди секунду и попробуй снова.')
            return

        file_id = message.photo[-1].file_id
        state = ensure_state(chat_id)

        if 'body_path' not in state:
            try:
                body_path = get_temp_path(chat_id, 'body.jpg')
                save_photo(file_id, body_path)
                state['body_path'] = body_path
                bot.reply_to(message, 'Фото тела сохранено. Теперь отправь эскиз тату.', reply_markup=build_keyboard(chat_id))
            except ValueError as exc:
                bot.reply_to(message, f'Ошибка: {exc}')
            except Exception as exc:
                logger.exception('Ошибка сохранения фото для чата %s', chat_id)
                bot.reply_to(message, 'Не удалось сохранить фото. Попробуй ещё раз.')
            return

        body_path = state.get('body_path')
        tattoo_path = get_temp_path(chat_id, 'tattoo.png')
        result_path = get_temp_path(chat_id, 'result.jpg')

        try:
            if not body_path or not os.path.exists(str(body_path)):
                raise FileNotFoundError('Фото тела не найдено. Начни заново.')

            save_photo(file_id, tattoo_path)
            apply_tattoo(str(body_path), tattoo_path, result_path, use_ai=state.get('mode') == 'ai')
            with open(result_path, 'rb') as photo_file:
                mode_label = '🤖 AI' if state.get('mode') == 'ai' else '🧩 стандарт'
                bot.send_photo(chat_id, photo_file, caption=f'Готово! Режим: {mode_label}', reply_markup=build_keyboard(chat_id))
        except ValueError as exc:
            bot.reply_to(message, f'Ошибка: {exc}')
        except Exception as exc:
            logger.exception('Ошибка обработки изображения для чата %s', chat_id)
            bot.reply_to(message, 'Ошибка обработки. Попробуй ещё раз.')
        finally:
            cleanup_chat_files(chat_id, body_path=str(body_path) if body_path else None, tattoo_path=tattoo_path, result_path=result_path)


def start_bot_polling() -> None:
    logger.info('Запуск Telegram polling')
    bot.remove_webhook()
    try:
        bot.infinity_polling(timeout=10, long_polling_timeout=10)
    except ApiTelegramException as exc:
        error_code = getattr(exc, 'result_json', {}).get('error_code') if hasattr(exc, 'result_json') else None
        if error_code == 409 or '409' in str(exc):
            logger.error('Telegram conflict 409: другой экземпляр бота уже запущен. Завершаю процесс.')
            raise SystemExit(1) from exc
        logger.exception('ApiTelegramException при запуске polling')
        raise SystemExit(1) from exc
    except Exception as exc:
        logger.exception('Ошибка запуска polling для Telegram-бота')
        raise SystemExit(1) from exc


if __name__ == '__main__':
    start_bot_polling()
