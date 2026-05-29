import csv
import math
import os
import asyncio
from datetime import datetime

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.types import FSInputFile

from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.lib.pagesizes import A4
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Image,
    Table, TableStyle, HRFlowable
)
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

API_TOKEN = '8360048034:AAFhMutaAqRBCElG4blTcIc2fU73qPxOgSI'

# Имя менеджера — впишите своё
MANAGER_NAME = "Менеджер"

bot = Bot(token=API_TOKEN)
dp = Dispatcher()

# Состояние каждого пользователя хранится отдельно
# Шаги (user_state[uid]["step"]):
#   "perimeter" → "fio" → "address" → "extra" → генерация PDF
user_state: dict[int, dict] = {}


# ─────────────────────────────────────────────
# Загрузка цен
# ─────────────────────────────────────────────
def load_prices(filename="prices.csv"):
    prices = {}
    with open(filename, newline='', encoding="utf-8") as f:
        first_line = f.readline()
        delimiter = ';' if ';' in first_line else ','
        f.seek(0)
        reader = csv.DictReader(f, delimiter=delimiter)
        reader.fieldnames = [name.strip() for name in reader.fieldnames]
        if "key" not in reader.fieldnames or "price" not in reader.fieldnames:
            raise ValueError(f"CSV должен содержать 'key' и 'price'. Найдено: {reader.fieldnames}")
        for row in reader:
            prices[row["key"].strip()] = float(row["price"].strip())
    return prices


# ─────────────────────────────────────────────
# Расчёт сметы
# ─────────────────────────────────────────────
def calculate(perimeter, material, zazor, kalitka, vorota, demontazh, distance=0):
    prices = load_prices()

    zabor_name   = ""
    zazor_name   = "Без зазора"
    kalitka_name = "Без калитки"
    vorota_name  = "Без ворот"

    zena_kalitka = 0
    vorota_zena  = 0
    zena         = 0
    kolvo        = 0
    obsh_krepezh = 0

    if kalitka == 1:
        kalitka_name = "Калитка эконом"
        zena_kalitka = prices["kalitka_eco"]
    elif kalitka == 2:
        kalitka_name = "Калитка с замком"
        zena_kalitka = prices["kalitka_lock"]

    vorota_key = f"vorota_{vorota}"
    if vorota_key in prices:
        vorota_name = f"Ворота вариант {vorota}"
        vorota_zena = prices[vorota_key]

    kolvo_stolbov = math.floor(perimeter / 2.5) + 1
    if material in [1, 2]:
        zena_stolbov = kolvo_stolbov * prices["stolb_3d"]
    else:
        zena_stolbov = kolvo_stolbov * prices["stolb_shtaket"]

    sheben_zena  = perimeter * prices["sheben_per_m"]
    montage_zena = perimeter * prices["montage_per_m"]
    dem_zena     = perimeter * prices["demontazh_per_m"] if demontazh else 0
    dostavka_zena = distance * prices["dostavka_km"]

    if material in [1, 2]:
        zabor_name = "3D сетка"
        kolvo = perimeter / 2.5
        zena  = kolvo * (prices["setka_3d_eco"] if material == 1 else prices["setka_3d_premium"])
        obsh_krepezh = 6 * kolvo * prices["krepezh_setka"]

    elif material == 3:
        zabor_name = "Профлист"
        kolvo = perimeter / 1.1
        zena  = kolvo * prices["proflist"]
        obsh_krepezh = 8 * kolvo * prices["krepezh_proflist"]

    else:
        zabor_name = "Штакетник"
        zazor_map = {
            1: (7.6, "2 см"),
            2: (7.6, "2 см двустор."),
            3: (6.6, "4 см"),
            4: (6.6, "4 см двустор."),
            5: (11,  "6 см шахм."),
            6: (10,  "8 см шахм."),
        }
        if zazor in zazor_map:
            koef, zazor_name = zazor_map[zazor]
            kolvo = perimeter * koef

        if zazor in [1, 3] and material == 4:
            zena = kolvo * prices["shtaket_175"]
        elif zazor in [2, 4, 5, 6] and material == 4:
            zena = kolvo * prices["shtaket_185"]
        elif zazor in [2, 4, 5, 6] and material == 5:
            zena = kolvo * prices["shtaket_200"]
        else:
            zena = kolvo * prices["shtaket_190"]

        obsh_krepezh = ((kolvo * 2) + (kolvo * 4)) * prices["krepezh_shtaket"]

    obshaya_zena = (zena + zena_kalitka + vorota_zena + sheben_zena
                    + dem_zena + montage_zena + obsh_krepezh)

    return {
        "zabor_name":   zabor_name,
        "kolvo":        kolvo,
        "zena":         zena,
        "zazor_name":   zazor_name,
        "kalitka_name": kalitka_name,
        "zena_kalitka": zena_kalitka,
        "vorota_name":  vorota_name,
        "vorota_zena":  vorota_zena,
        "sheben_zena":  sheben_zena,
        "dem_zena":     dem_zena,
        "montage_zena": montage_zena,
        "obshaya_zena": obshaya_zena,
    }


# ─────────────────────────────────────────────
# Форматирование текста для чата
# ─────────────────────────────────────────────
def format_result(r: dict, fio="", address="", extra="") -> str:
    date_str = datetime.now().strftime("%d.%m.%Y")
    lines = ["📋 СМЕТА", f"Дата: {date_str}"]
    if fio:     lines.append(f"Клиент: {fio}")
    if address: lines.append(f"Адрес объекта: {address}")
    lines.append("─" * 32)
    lines.append(f"Забор: {r['zabor_name']}, {round(r['kolvo'])} шт. — {round(r['zena'])} руб.")
    lines.append(f"Зазор: {r['zazor_name']}")
    lines.append(f"Калитка: {r['kalitka_name']} — {round(r['zena_kalitka'])} руб.")
    lines.append(f"Ворота: {r['vorota_name']} — {round(r['vorota_zena'])} руб.")
    lines.append(f"Щебень: {round(r['sheben_zena'])} руб.")
    lines.append(f"Демонтаж: {round(r['dem_zena'])} руб.")
    lines.append(f"Монтаж: {round(r['montage_zena'])} руб.")
    if extra:
        lines.append("─" * 32)
        lines.append(f"Доп. опции: {extra}")
    lines.append("─" * 32)
    lines.append(f"💰 ИТОГО: {round(r['obshaya_zena'])} руб.")
    lines.append("+ доставка рассчитывается индивидуально")
    return "\n".join(lines)


# ─────────────────────────────────────────────
# Генерация красивого PDF
# ─────────────────────────────────────────────
def build_pdf(r: dict, fio="", address="", extra="", manager=MANAGER_NAME) -> str:
    BASE_DIR  = os.path.dirname(os.path.abspath(__file__))
    font_path = os.path.join(BASE_DIR, "DejaVuSans.ttf")
    font_bold = os.path.join(BASE_DIR, "DejaVuSans-Bold.ttf")
    logo_path = os.path.join(BASE_DIR, "logo.png")
    safe_fio  = (fio or "klient").replace(" ", "_")
    pdf_path  = os.path.join(BASE_DIR, f"smeta_{safe_fio}.pdf")

    # Регистрация шрифтов
    if "DejaVu" not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont("DejaVu", font_path))
    if "DejaVuBold" not in pdfmetrics.getRegisteredFontNames() and os.path.exists(font_bold):
        pdfmetrics.registerFont(TTFont("DejaVuBold", font_bold))

    BOLD = "DejaVuBold" if "DejaVuBold" in pdfmetrics.getRegisteredFontNames() else "DejaVu"

    # Цвета
    C_DARK    = colors.HexColor("#1a2a3a")   # тёмно-синий — шапка таблицы
    C_ACCENT  = colors.HexColor("#2e86c1")   # синий — итого
    C_LIGHT   = colors.HexColor("#eaf4fb")   # светло-голубой — чётные строки
    C_WHITE   = colors.white
    C_BORDER  = colors.HexColor("#aed6f1")   # граница таблицы

    # Стили
    def S(name, font="DejaVu", size=10, color=colors.black, align="LEFT",
          leading=None, bold=False):
        return ParagraphStyle(
            name, fontName=BOLD if bold else font,
            fontSize=size, textColor=color,
            alignment={"LEFT": 0, "CENTER": 1, "RIGHT": 2}[align],
            leading=leading or size * 1.4,
        )

    s_title    = S("title",   size=20, bold=True, color=C_DARK,   align="CENTER")
    s_sub      = S("sub",     size=10, color=colors.grey,          align="CENTER")
    s_info     = S("info",    size=10, color=C_DARK)
    s_info_b   = S("info_b",  size=10, bold=True, color=C_DARK)
    s_th       = S("th",      size=10, bold=True, color=C_WHITE,   align="CENTER")
    s_td_l     = S("td_l",    size=10, color=C_DARK)
    s_td_r     = S("td_r",    size=10, color=C_DARK,               align="RIGHT")
    s_total_l  = S("tot_l",   size=11, bold=True, color=C_WHITE)
    s_total_r  = S("tot_r",   size=11, bold=True, color=C_WHITE,   align="RIGHT")
    s_extra    = S("extra",   size=9,  color=colors.grey)
    s_sign_hdr = S("sh",      size=9,  bold=True, color=C_DARK)
    s_sign_val = S("sv",      size=9,  color=colors.grey)

    W = A4[0] - 40*mm   # ширина контента
    doc = SimpleDocTemplate(
        pdf_path,
        pagesize=A4,
        leftMargin=20*mm, rightMargin=20*mm,
        topMargin=15*mm,  bottomMargin=15*mm,
    )

    elements = []

    # ── Логотип ──────────────────────────────
    if os.path.exists(logo_path):
        logo = Image(logo_path, width=50*mm, height=25*mm)
        logo.hAlign = "LEFT"
        elements.append(logo)
        elements.append(Spacer(1, 4*mm))

    # ── Заголовок ────────────────────────────
    date_str = datetime.now().strftime("%d.%m.%Y")
    elements.append(Paragraph("КОММЕРЧЕСКОЕ ПРЕДЛОЖЕНИЕ / СМЕТА", s_title))
    elements.append(Paragraph(f"Дата составления: {date_str}", s_sub))
    elements.append(Spacer(1, 5*mm))
    elements.append(HRFlowable(width="100%", thickness=1.5, color=C_ACCENT))
    elements.append(Spacer(1, 4*mm))

    # ── Инфо-блок (клиент / адрес) ───────────
    info_rows = []
    if fio:
        info_rows.append([Paragraph("Клиент:", s_info_b), Paragraph(fio, s_info)])
    if address:
        info_rows.append([Paragraph("Адрес объекта:", s_info_b), Paragraph(address, s_info)])

    if info_rows:
        info_table = Table(info_rows, colWidths=[40*mm, W - 40*mm])
        info_table.setStyle(TableStyle([
            ("VALIGN",     (0, 0), (-1, -1), "TOP"),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ]))
        elements.append(info_table)
        elements.append(Spacer(1, 5*mm))

    # ── Основная таблица сметы ────────────────
    def money(v): return f"{round(v):,} руб.".replace(",", " ")
    def qty(v):   return str(round(v))

    # Шапка
    header = [[
        Paragraph("№", s_th),
        Paragraph("Наименование", s_th),
        Paragraph("Кол-во", s_th),
        Paragraph("Сумма", s_th),
    ]]

    # Строки данных
    rows_data = [
        [r["zabor_name"],   qty(r["kolvo"]),  money(r["zena"])],
        [r["zazor_name"],   "—",             "—"],
        [r["kalitka_name"], "1",              money(r["zena_kalitka"]) if r["zena_kalitka"] else "—"],
        [r["vorota_name"],  "1",              money(r["vorota_zena"])  if r["vorota_zena"]  else "—"],
        ["Щебень",          "—",             money(r["sheben_zena"])],
        ["Демонтаж",        "—",             money(r["dem_zena"]) if r["dem_zena"] else "—"],
        ["Монтаж",          "—",             money(r["montage_zena"])],
    ]

    body = []
    for i, (name, qty_v, sum_v) in enumerate(rows_data):
        bg = C_LIGHT if i % 2 == 0 else C_WHITE
        body.append([
            Paragraph(str(i + 1),  s_td_l),
            Paragraph(name,        s_td_l),
            Paragraph(qty_v,       s_td_r),
            Paragraph(sum_v,       s_td_r),
        ])

    # Итого-строка
    total_row = [[
        Paragraph("",                         s_total_l),
        Paragraph("ИТОГО",                    s_total_l),
        Paragraph("",                         s_total_l),
        Paragraph(money(r["obshaya_zena"]),   s_total_r),
    ]]

    col_w = [10*mm, W - 10*mm - 25*mm - 45*mm, 25*mm, 45*mm]
    full_table_data = header + body + total_row
    n_body = len(body)

    table = Table(full_table_data, colWidths=col_w, repeatRows=1)

    row_styles = []
    # Чётные строки данных — светлый фон (строки 1..n_body, шапка = строка 0)
    for i in range(n_body):
        if i % 2 == 0:
            row_styles.append(("BACKGROUND", (0, i+1), (-1, i+1), C_LIGHT))
        else:
            row_styles.append(("BACKGROUND", (0, i+1), (-1, i+1), C_WHITE))

    table.setStyle(TableStyle([
        # Шапка
        ("BACKGROUND",    (0, 0), (-1, 0),      C_DARK),
        ("ROWBACKGROUNDS",(0, 0), (-1, 0),      [C_DARK]),
        ("FONTNAME",      (0, 0), (-1, 0),      BOLD),
        ("FONTSIZE",      (0, 0), (-1, 0),      10),
        ("TEXTCOLOR",     (0, 0), (-1, 0),      C_WHITE),
        ("ALIGN",         (0, 0), (-1, 0),      "CENTER"),
        # Итого
        ("BACKGROUND",    (0, -1), (-1, -1),    C_ACCENT),
        ("FONTNAME",      (0, -1), (-1, -1),    BOLD),
        ("TEXTCOLOR",     (0, -1), (-1, -1),    C_WHITE),
        # Общее
        ("GRID",          (0, 0), (-1, -2),     0.5, C_BORDER),
        ("LINEBELOW",     (0, -1), (-1, -1),    1.5, C_ACCENT),
        ("VALIGN",        (0, 0), (-1, -1),     "MIDDLE"),
        ("TOPPADDING",    (0, 0), (-1, -1),     5),
        ("BOTTOMPADDING", (0, 0), (-1, -1),     5),
        ("LEFTPADDING",   (0, 0), (-1, -1),     6),
        ("RIGHTPADDING",  (0, 0), (-1, -1),     6),
        *row_styles,
    ]))

    elements.append(table)
    elements.append(Spacer(1, 3*mm))

    # ── Доп. опции ────────────────────────────
    if extra:
        elements.append(Paragraph(f"Дополнительные опции: {extra}", s_extra))
        elements.append(Spacer(1, 3*mm))

    # ── Примечание о доставке ─────────────────
    elements.append(Paragraph(
        "* Стоимость доставки рассчитывается индивидуально и в смету не включена.",
        s_extra
    ))
    elements.append(Spacer(1, 8*mm))
    elements.append(HRFlowable(width="100%", thickness=0.5, color=C_BORDER))
    elements.append(Spacer(1, 6*mm))

    # ── Подписи ───────────────────────────────
    sign_col = W / 2 - 5*mm
    sign_data = [[
        # Клиент
        Table([
            [Paragraph("Клиент:", s_sign_hdr)],
            [Paragraph(fio or "____________________", s_sign_val)],
            [Spacer(1, 8*mm)],
            [HRFlowable(width="100%", thickness=0.5, color=colors.grey)],
            [Paragraph("подпись / дата", s_sign_val)],
        ], colWidths=[sign_col]),
        # Менеджер
        Table([
            [Paragraph("Менеджер:", s_sign_hdr)],
            [Paragraph(manager, s_sign_val)],
            [Spacer(1, 8*mm)],
            [HRFlowable(width="100%", thickness=0.5, color=colors.grey)],
            [Paragraph("подпись / дата", s_sign_val)],
        ], colWidths=[sign_col]),
    ]]

    sign_table = Table(sign_data, colWidths=[sign_col + 5*mm, sign_col + 5*mm])
    sign_table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))
    elements.append(sign_table)

    doc.build(elements)
    return pdf_path


# ─────────────────────────────────────────────
# ХЕНДЛЕРЫ
# ─────────────────────────────────────────────

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    await message.answer(
        "Приветствую!\n"
        "Это бот для создания сметы на забор.\n\n"
        "/mech — создать смету\n"
        "/inf — справка"
    )


@dp.message(Command("inf"))
async def cmd_inf(message: types.Message):
    await message.answer(
        "Типы заборов:\n"
        "1: 3D Сетка 1.5м\n"
        "2: 3D Сетка 1.8м\n"
        "3: Профлист\n"
        "4: Штакетник 1.8м\n"
        "5: Штакетник 2м\n\n"
        "Ворота:\n"
        "1: Эконом — 21 000 руб.\n"
        "2: До 4 м — 80 000 руб.\n"
        "3: До 5 м — 90 000 руб.\n"
        "14: Эконом + автоматика — 84 000 руб.\n"
        "24: До 4 м + автоматика — 143 000 руб.\n"
        "34: До 5 м + автоматика — 153 000 руб.\n\n"
        "Расчёт доставки — индивидуально."
    )


@dp.message(Command("mech"))
async def cmd_mech(message: types.Message):
    user_state[message.from_user.id] = {"step": "perimeter"}
    await message.answer("Введи периметр объекта (в метрах):")


# ── Единый текстовый обработчик ──────────────
@dp.message(F.text)
async def handle_text(message: types.Message):
    uid   = message.from_user.id
    state = user_state.get(uid, {})
    step  = state.get("step")

    # Периметр
    if step == "perimeter":
        try:
            num = int(message.text)
            if num <= 0:
                raise ValueError
        except ValueError:
            await message.answer("Введи целое положительное число:")
            return
        state["per"]  = num
        state["step"] = None
        kb = InlineKeyboardBuilder()
        kb.button(text="🔹 3D Сетка 1.5м",  callback_data="zab_0")
        kb.button(text="🔹 3D Сетка 1.8м",  callback_data="zab_1")
        kb.button(text="🔹 Профлист",        callback_data="zab_2")
        kb.button(text="🔹 Штакетник 1.8м", callback_data="zab_3")
        kb.button(text="🔹 Штакетник 2м",   callback_data="zab_4")
        kb.adjust(1)
        await message.answer(f"Периметр: {num} м\nВыбери тип забора:", reply_markup=kb.as_markup())

    # ФИО
    elif step == "fio":
        val = message.text.strip()
        if not val:
            await message.answer("ФИО не может быть пустым, попробуй ещё раз:")
            return
        state["fio"]  = val
        state["step"] = "address"
        await message.answer(
            "Введи адрес объекта\n"
            "(например: г. Москва, ул. Садовая, д. 5):"
        )

    # Адрес
    elif step == "address":
        val = message.text.strip()
        if not val:
            await message.answer("Адрес не может быть пустым, попробуй ещё раз:")
            return
        state["address"] = val
        state["step"]    = "extra"
        await message.answer(
            "Введи дополнительные опции или примечания\n"
            "(покраска, грунтовка, козырёк и т.п.)\n\n"
            "Если ничего нет — напиши <b>нет</b> или <b>-</b>",
            parse_mode="HTML"
        )

    # Доп. опции
    elif step == "extra":
        val = message.text.strip()
        state["extra"] = "" if val.lower() in ("нет", "-", "no", "н", ".") else val
        state["step"]  = None
        await do_generate(message, state)

    else:
        await message.answer("Используй /mech для создания сметы.")


# ── Генерация и отправка ─────────────────────
async def do_generate(source: types.Message, state: dict):
    perimeter = state.get("per", 0)
    material  = state.get("zab", 0) + 1
    zazor     = state.get("zaz", 1)
    kalitka   = state.get("kal", 0)
    vorota    = state.get("vor", 0)
    demontazh = state.get("dem", 0)
    fio       = state.get("fio", "")
    address   = state.get("address", "")
    extra     = state.get("extra", "")

    try:
        r        = calculate(perimeter, material, zazor, kalitka, vorota, demontazh)
        chat_txt = format_result(r, fio, address, extra)
        await source.answer(chat_txt)

        pdf_path = build_pdf(r, fio, address, extra)
        file     = FSInputFile(pdf_path)
        caption  = f"Смета для {fio}" if fio else "Смета"
        await source.answer_document(file, caption=caption)
        os.remove(pdf_path)

    except Exception as e:
        await source.answer(f"Ошибка при расчёте: {e}")


# ── Inline-хендлеры ──────────────────────────

@dp.callback_query(lambda c: c.data.startswith("zab_"))
async def handle_zab(callback: types.CallbackQuery):
    uid = callback.from_user.id
    user_state.setdefault(uid, {})
    user_state[uid]["zab"] = int(callback.data.split("_")[1])
    await callback.answer()
    kb = InlineKeyboardBuilder()
    kb.button(text="🔹 Без калитки",       callback_data="kal_0")
    kb.button(text="🔹 Калитка без замка", callback_data="kal_1")
    kb.button(text="🔹 Калитка с замком",  callback_data="kal_2")
    kb.adjust(1)
    await callback.message.answer("Выберите калитку:", reply_markup=kb.as_markup())


@dp.callback_query(lambda c: c.data.startswith("kal_"))
async def handle_kal(callback: types.CallbackQuery):
    uid = callback.from_user.id
    user_state.setdefault(uid, {})
    user_state[uid]["kal"] = int(callback.data.split("_")[1])
    await callback.answer()
    kb = InlineKeyboardBuilder()
    kb.button(text="🔹 Без ворот",                 callback_data="vor_0")
    kb.button(text="🔹 Ворота эконом",             callback_data="vor_1")
    kb.button(text="🔹 Ворота до 4м",              callback_data="vor_2")
    kb.button(text="🔹 Ворота до 5м",              callback_data="vor_3")
    kb.button(text="🔹 Эконом + автоматика",       callback_data="vor_14")
    kb.button(text="🔹 Ворота до 4м + автоматика", callback_data="vor_24")
    kb.button(text="🔹 Ворота до 5м + автоматика", callback_data="vor_34")
    kb.adjust(1)
    await callback.message.answer("Выберите ворота:", reply_markup=kb.as_markup())


@dp.callback_query(lambda c: c.data.startswith("vor_"))
async def handle_vor(callback: types.CallbackQuery):
    uid = callback.from_user.id
    user_state.setdefault(uid, {})
    user_state[uid]["vor"] = int(callback.data.split("_")[1])
    await callback.answer()
    zab = user_state[uid].get("zab", 0)
    if zab in [3, 4]:
        kb = InlineKeyboardBuilder()
        kb.button(text="🔹 2 см",          callback_data="zaz_1")
        kb.button(text="🔹 2 см двустор.", callback_data="zaz_2")
        kb.button(text="🔹 4 см",          callback_data="zaz_3")
        kb.button(text="🔹 4 см двустор.", callback_data="zaz_4")
        kb.button(text="🔹 6 см шахм.",    callback_data="zaz_5")
        kb.button(text="🔹 8 см шахм.",    callback_data="zaz_6")
        kb.adjust(1)
        await callback.message.answer("Выберите зазор штакетника:", reply_markup=kb.as_markup())
    else:
        user_state[uid]["zaz"] = 1
        await ask_demontazh(callback)


@dp.callback_query(lambda c: c.data.startswith("zaz_"))
async def handle_zaz(callback: types.CallbackQuery):
    uid = callback.from_user.id
    user_state.setdefault(uid, {})
    user_state[uid]["zaz"] = int(callback.data.split("_")[1])
    await callback.answer()
    await ask_demontazh(callback)


async def ask_demontazh(callback: types.CallbackQuery):
    kb = InlineKeyboardBuilder()
    kb.button(text="🔹 Демонтаж",      callback_data="dem_1")
    kb.button(text="🔹 Без демонтажа", callback_data="dem_0")
    kb.adjust(1)
    await callback.message.answer("Нужен демонтаж?", reply_markup=kb.as_markup())


@dp.callback_query(lambda c: c.data.startswith("dem_"))
async def handle_dem(callback: types.CallbackQuery):
    uid   = callback.from_user.id
    state = user_state.setdefault(uid, {})
    state["dem"]  = int(callback.data.split("_")[1])
    state["step"] = "fio"
    await callback.answer()
    await callback.message.answer(
        "Введите ФИО клиента\n"
        "(например: Иванов Иван Иванович):"
    )


# ─────────────────────────────────────────────
async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
