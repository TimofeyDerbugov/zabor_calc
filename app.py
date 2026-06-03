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

API_TOKEN    = os.getenv('API_TOKEN', 'ЗАМЕНИ_НА_ТОКЕН')
MANAGER_NAME = os.getenv('MANAGER_NAME', 'Менеджер')

bot = Bot(token=API_TOKEN)
dp  = Dispatcher()

user_state: dict[int, dict] = {}

# ─────────────────────────────────────────────
# Справочники меток
# ─────────────────────────────────────────────
ZAB_LABELS = {0: "3D Сетка 1.5м", 1: "3D Сетка 1.8м", 2: "Профлист",
              3: "Штакетник 1.8м", 4: "Штакетник 2м"}
KAL_LABELS = {0: "Без калитки", 1: "Калитка без замка", 2: "Калитка с замком"}
VOR_LABELS = {0: "Без ворот", 1: "Ворота эконом", 2: "Ворота до 4м",
              3: "Ворота до 5м", 14: "Эконом + авт.", 24: "До 4м + авт.", 34: "До 5м + авт."}
ZAZ_LABELS = {1: "2 см", 2: "2 см двустор.", 3: "4 см",
              4: "4 см двустор.", 5: "6 см шахм.", 6: "8 см шахм."}
DEM_LABELS = {0: "Без демонтажа", 1: "Демонтаж"}


# ─────────────────────────────────────────────
# Загрузка цен
# ─────────────────────────────────────────────
def load_prices(filename="prices.csv"):
    prices = {}
    with open(filename, newline='', encoding="utf-8") as f:
        first_line = f.readline()
        delimiter  = ';' if ';' in first_line else ','
        f.seek(0)
        reader = csv.DictReader(f, delimiter=delimiter)
        reader.fieldnames = [n.strip() for n in reader.fieldnames]
        if "key" not in reader.fieldnames or "price" not in reader.fieldnames:
            raise ValueError(f"CSV должен содержать 'key' и 'price'. Найдено: {reader.fieldnames}")
        for row in reader:
            prices[row["key"].strip()] = float(row["price"].strip())
    return prices


# ─────────────────────────────────────────────
# Расчёт сметы — возвращает dict с колво и ценами
# ─────────────────────────────────────────────
def calculate(perimeter, material, zazor, kalitka, vorota, demontazh):
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

    sheben_zena  = perimeter * prices["sheben_per_m"]
    montage_zena = perimeter * prices["montage_per_m"]
    dem_zena     = perimeter * prices["demontazh_per_m"] if demontazh else 0

    if material in [1, 2]:
        zabor_name   = "3D сетка"
        kolvo        = perimeter / 2.5
        zena         = kolvo * (prices["setka_3d_eco"] if material == 1 else prices["setka_3d_premium"])
        obsh_krepezh = 6 * kolvo * prices["krepezh_setka"]
    elif material == 3:
        zabor_name   = "Профлист"
        kolvo        = perimeter / 1.1
        zena         = kolvo * prices["proflist"]
        obsh_krepezh = 8 * kolvo * prices["krepezh_proflist"]
    else:
        zabor_name = "Штакетник"
        zazor_map  = {
            1: (7.6, "2 см"), 2: (7.6, "2 см двустор."),
            3: (6.6, "4 см"), 4: (6.6, "4 см двустор."),
            5: (11, "6 см шахм."), 6: (10, "8 см шахм."),
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

    obshaya_zena = zena + zena_kalitka + vorota_zena + sheben_zena + dem_zena + montage_zena + obsh_krepezh

    return dict(
        zabor_name=zabor_name, kolvo=kolvo, zena=zena,
        zazor_name=zazor_name, kalitka_name=kalitka_name,
        zena_kalitka=zena_kalitka, vorota_name=vorota_name,
        vorota_zena=vorota_zena, sheben_zena=sheben_zena,
        dem_zena=dem_zena, montage_zena=montage_zena,
        obshaya_zena=obshaya_zena,
    )


# ─────────────────────────────────────────────
# Применяем ручные правки поверх расчёта
# state["overrides"] = {"kolvo": 42, "zena": 50000, ...}
# ─────────────────────────────────────────────
def apply_overrides(r: dict, overrides: dict) -> dict:
    r = dict(r)
    for key, val in overrides.items():
        r[key] = val
    # пересчитываем итог после правок
    r["obshaya_zena"] = (
        r["zena"] + r["zena_kalitka"] + r["vorota_zena"] +
        r["sheben_zena"] + r["dem_zena"] + r["montage_zena"]
    )
    return r


# ─────────────────────────────────────────────
# Получить итоговый результат с учётом правок
# ─────────────────────────────────────────────
def get_result(state: dict) -> dict:
    r = calculate(
        state.get("per", 0),
        state.get("zab", 0) + 1,
        state.get("zaz", 1),
        state.get("kal", 0),
        state.get("vor", 0),
        state.get("dem", 0),
    )
    overrides = state.get("overrides", {})
    if overrides:
        r = apply_overrides(r, overrides)
    return r


# ─────────────────────────────────────────────
# Сводка с рассчитанными данными
# ─────────────────────────────────────────────
def format_review(state: dict) -> str:
    r   = get_result(state)
    zab = state.get("zab", 0)
    ov  = state.get("overrides", {})

    def mark(key):
        return " ✏️" if key in ov else ""

    def money(v): return f"{round(v):,} руб.".replace(",", " ")

    lines = ["📝 <b>Проверьте данные и суммы:</b>\n"]

    lines.append(
        f"🪨 Забор: <b>{r['zabor_name']}</b>, "
        f"кол-во: <b>{round(r['kolvo'])} шт.</b>{mark('kolvo')}, "
        f"сумма: <b>{money(r['zena'])}</b>{mark('zena')}"
    )
    if zab in [3, 4]:
        lines.append(f"   Зазор: <b>{r['zazor_name']}</b>")

    lines.append(
        f"🚪 Калитка: <b>{r['kalitka_name']}</b> — "
        f"<b>{money(r['zena_kalitka'])}</b>{mark('zena_kalitka')}"
    )
    lines.append(
        f"🚗 Ворота: <b>{r['vorota_name']}</b> — "
        f"<b>{money(r['vorota_zena'])}</b>{mark('vorota_zena')}"
    )
    lines.append(f"🪨 Щебень: <b>{money(r['sheben_zena'])}</b>{mark('sheben_zena')}")
    lines.append(f"🔨 Демонтаж: <b>{money(r['dem_zena'])}</b>{mark('dem_zena')}")
    lines.append(f"🔧 Монтаж: <b>{money(r['montage_zena'])}</b>{mark('montage_zena')}")
    lines.append(f"\n💰 <b>ИТОГО: {money(r['obshaya_zena'])}</b>")
    lines.append("─" * 32)
    lines.append(f"👤 ФИО: <b>{state.get('fio', '—')}</b>")
    lines.append(f"📍 Адрес: <b>{state.get('address', '—')}</b>")
    lines.append(f"📋 Доп. опции: <b>{state.get('extra') or '—'}</b>")

    if ov:
        lines.append("\n<i>✏️ — значение изменено вручную</i>")

    return "\n".join(lines)


def review_keyboard(state: dict):
    kb  = InlineKeyboardBuilder()
    zab = state.get("zab", 0)

    # Параметры заказа
    kb.button(text="✏️ Тип забора",    callback_data="edit_zab")
    kb.button(text="✏️ Кол-во забора", callback_data="edit_kolvo")
    kb.button(text="✏️ Цена забора",   callback_data="edit_zena")
    if zab in [3, 4]:
        kb.button(text="✏️ Зазор",     callback_data="edit_zaz")
    kb.button(text="✏️ Калитка",       callback_data="edit_kal")
    kb.button(text="✏️ Цена калитки",  callback_data="edit_zena_kalitka")
    kb.button(text="✏️ Ворота",        callback_data="edit_vor")
    kb.button(text="✏️ Цена ворот",    callback_data="edit_vorota_zena")
    kb.button(text="✏️ Щебень",        callback_data="edit_sheben_zena")
    kb.button(text="✏️ Демонтаж",      callback_data="edit_dem")
    kb.button(text="✏️ Цена демонтажа",callback_data="edit_dem_zena")
    kb.button(text="✏️ Цена монтажа",  callback_data="edit_montage_zena")
    # Клиент
    kb.button(text="✏️ ФИО",           callback_data="edit_fio")
    kb.button(text="✏️ Адрес",         callback_data="edit_address")
    kb.button(text="✏️ Доп. опции",    callback_data="edit_extra")
    # Сброс правок
    kb.button(text="🔄 Сбросить правки", callback_data="reset_overrides")
    # Подтверждение
    kb.button(text="✅ Всё верно, создать смету", callback_data="confirm_generate")
    kb.adjust(1, 2, 2, 2, 2, 2, 1, 1, 1, 1)
    return kb.as_markup()


async def show_review(target, state: dict):
    text = format_review(state)
    kb   = review_keyboard(state)
    msg  = target if isinstance(target, types.Message) else target.message
    await msg.answer(text, reply_markup=kb, parse_mode="HTML")


# ─────────────────────────────────────────────
# Форматирование готовой сметы для чата
# ─────────────────────────────────────────────
def format_result(r: dict, fio="", address="", extra="") -> str:
    def money(v): return f"{round(v):,} руб.".replace(",", " ")
    date_str = datetime.now().strftime("%d.%m.%Y")
    lines = ["📋 <b>СМЕТА</b>", f"Дата: {date_str}"]
    if fio:     lines.append(f"Клиент: {fio}")
    if address: lines.append(f"Адрес объекта: {address}")
    lines.append("─" * 32)
    lines.append(f"Забор: {r['zabor_name']}, {round(r['kolvo'])} шт. — {money(r['zena'])}")
    lines.append(f"Зазор: {r['zazor_name']}")
    lines.append(f"Калитка: {r['kalitka_name']} — {money(r['zena_kalitka'])}")
    lines.append(f"Ворота: {r['vorota_name']} — {money(r['vorota_zena'])}")
    lines.append(f"Щебень: {money(r['sheben_zena'])}")
    lines.append(f"Демонтаж: {money(r['dem_zena'])}")
    lines.append(f"Монтаж: {money(r['montage_zena'])}")
    if extra:
        lines.append("─" * 32)
        lines.append(f"Доп. опции: {extra}")
    lines.append("─" * 32)
    lines.append(f"💰 <b>ИТОГО: {money(r['obshaya_zena'])}</b>")
    lines.append("+ доставка рассчитывается индивидуально")
    return "\n".join(lines)


# ─────────────────────────────────────────────
# Генерация PDF
# ─────────────────────────────────────────────
def build_pdf(r: dict, fio="", address="", extra="", manager=None) -> str:
    if manager is None:
        manager = MANAGER_NAME

    BASE_DIR  = os.path.dirname(os.path.abspath(__file__))
    font_path = os.path.join(BASE_DIR, "DejaVuSans.ttf")
    font_bold = os.path.join(BASE_DIR, "DejaVuSans-Bold.ttf")
    logo_path = os.path.join(BASE_DIR, "logo.png")
    safe_fio  = (fio or "klient").replace(" ", "_")
    pdf_path  = os.path.join(BASE_DIR, f"smeta_{safe_fio}.pdf")

    if "DejaVu" not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont("DejaVu", font_path))
    if "DejaVuBold" not in pdfmetrics.getRegisteredFontNames() and os.path.exists(font_bold):
        pdfmetrics.registerFont(TTFont("DejaVuBold", font_bold))

    BOLD = "DejaVuBold" if "DejaVuBold" in pdfmetrics.getRegisteredFontNames() else "DejaVu"

    C_DARK   = colors.HexColor("#1a2a3a")
    C_ACCENT = colors.HexColor("#2e86c1")
    C_LIGHT  = colors.HexColor("#eaf4fb")
    C_WHITE  = colors.white
    C_BORDER = colors.HexColor("#aed6f1")

    def S(name, font="DejaVu", size=10, color=colors.black, align="LEFT", leading=None, bold=False):
        return ParagraphStyle(
            name, fontName=BOLD if bold else font,
            fontSize=size, textColor=color,
            alignment={"LEFT": 0, "CENTER": 1, "RIGHT": 2}[align],
            leading=leading or size * 1.4,
        )

    s_title   = S("title",  size=20, bold=True, color=C_DARK,  align="CENTER")
    s_sub     = S("sub",    size=10, color=colors.grey,         align="CENTER")
    s_info    = S("info",   size=10, color=C_DARK)
    s_info_b  = S("info_b", size=10, bold=True, color=C_DARK)
    s_th      = S("th",     size=10, bold=True, color=C_WHITE,  align="CENTER")
    s_td_l    = S("td_l",   size=10, color=C_DARK)
    s_td_r    = S("td_r",   size=10, color=C_DARK,              align="RIGHT")
    s_total_l = S("tot_l",  size=11, bold=True, color=C_WHITE)
    s_total_r = S("tot_r",  size=11, bold=True, color=C_WHITE,  align="RIGHT")
    s_extra   = S("extra",  size=9,  color=colors.grey)
    s_sign_h  = S("sh",     size=9,  bold=True, color=C_DARK)
    s_sign_v  = S("sv",     size=9,  color=colors.grey)

    def money(v): return f"{round(v):,} руб.".replace(",", " ")
    def qty(v):   return str(round(v))

    W   = A4[0] - 40 * mm
    doc = SimpleDocTemplate(
        pdf_path, pagesize=A4,
        leftMargin=20*mm, rightMargin=20*mm,
        topMargin=15*mm,  bottomMargin=15*mm,
    )
    elements = []

    if os.path.exists(logo_path):
        logo = Image(logo_path, width=50*mm, height=25*mm)
        logo.hAlign = "LEFT"
        elements += [logo, Spacer(1, 4*mm)]

    date_str = datetime.now().strftime("%d.%m.%Y")
    elements += [
        Paragraph("КОММЕРЧЕСКОЕ ПРЕДЛОЖЕНИЕ / СМЕТА", s_title),
        Paragraph(f"Дата составления: {date_str}", s_sub),
        Spacer(1, 5*mm),
        HRFlowable(width="100%", thickness=1.5, color=C_ACCENT),
        Spacer(1, 4*mm),
    ]

    info_rows = []
    if fio:     info_rows.append([Paragraph("Клиент:",        s_info_b), Paragraph(fio,     s_info)])
    if address: info_rows.append([Paragraph("Адрес объекта:", s_info_b), Paragraph(address, s_info)])
    if info_rows:
        t = Table(info_rows, colWidths=[40*mm, W - 40*mm])
        t.setStyle(TableStyle([
            ("VALIGN",        (0,0), (-1,-1), "TOP"),
            ("BOTTOMPADDING", (0,0), (-1,-1), 3),
        ]))
        elements += [t, Spacer(1, 5*mm)]

    header = [[
        Paragraph("№",            s_th),
        Paragraph("Наименование", s_th),
        Paragraph("Кол-во",       s_th),
        Paragraph("Сумма",        s_th),
    ]]
    rows_data = [
        [r["zabor_name"],   qty(r["kolvo"]), money(r["zena"])],
        [r["zazor_name"],   "—",            "—"],
        [r["kalitka_name"], "1",             money(r["zena_kalitka"]) if r["zena_kalitka"] else "—"],
        [r["vorota_name"],  "1",             money(r["vorota_zena"])  if r["vorota_zena"]  else "—"],
        ["Щебень",          "—",            money(r["sheben_zena"])],
        ["Демонтаж",        "—",            money(r["dem_zena"]) if r["dem_zena"] else "—"],
        ["Монтаж",          "—",            money(r["montage_zena"])],
    ]

    body       = []
    row_styles = []
    for i, (name, q, s) in enumerate(rows_data):
        body.append([
            Paragraph(str(i+1), s_td_l),
            Paragraph(name,     s_td_l),
            Paragraph(q,        s_td_r),
            Paragraph(s,        s_td_r),
        ])
        row_styles.append(("BACKGROUND", (0, i+1), (-1, i+1), C_LIGHT if i % 2 == 0 else C_WHITE))

    total_row = [[
        Paragraph("",                       s_total_l),
        Paragraph("ИТОГО",                  s_total_l),
        Paragraph("",                       s_total_l),
        Paragraph(money(r["obshaya_zena"]), s_total_r),
    ]]

    col_w = [10*mm, W - 10*mm - 25*mm - 45*mm, 25*mm, 45*mm]
    table = Table(header + body + total_row, colWidths=col_w, repeatRows=1)
    table.setStyle(TableStyle([
        ("BACKGROUND",    (0,0),  (-1,0),  C_DARK),
        ("FONTNAME",      (0,0),  (-1,0),  BOLD),
        ("TEXTCOLOR",     (0,0),  (-1,0),  C_WHITE),
        ("ALIGN",         (0,0),  (-1,0),  "CENTER"),
        ("BACKGROUND",    (0,-1), (-1,-1), C_ACCENT),
        ("FONTNAME",      (0,-1), (-1,-1), BOLD),
        ("TEXTCOLOR",     (0,-1), (-1,-1), C_WHITE),
        ("GRID",          (0,0),  (-1,-2), 0.5, C_BORDER),
        ("LINEBELOW",     (0,-1), (-1,-1), 1.5, C_ACCENT),
        ("VALIGN",        (0,0),  (-1,-1), "MIDDLE"),
        ("TOPPADDING",    (0,0),  (-1,-1), 5),
        ("BOTTOMPADDING", (0,0),  (-1,-1), 5),
        ("LEFTPADDING",   (0,0),  (-1,-1), 6),
        ("RIGHTPADDING",  (0,0),  (-1,-1), 6),
        *row_styles,
    ]))
    elements += [table, Spacer(1, 3*mm)]

    if extra:
        elements += [Paragraph(f"Дополнительные опции: {extra}", s_extra), Spacer(1, 3*mm)]

    elements += [
        Paragraph("* Стоимость доставки рассчитывается индивидуально.", s_extra),
        Spacer(1, 8*mm),
        HRFlowable(width="100%", thickness=0.5, color=C_BORDER),
        Spacer(1, 6*mm),
    ]

    sign_col  = W / 2 - 5*mm
    sign_data = [[
        Table([
            [Paragraph("Клиент:", s_sign_h)],
            [Paragraph(fio or "____________________", s_sign_v)],
            [Spacer(1, 8*mm)],
            [HRFlowable(width="100%", thickness=0.5, color=colors.grey)],
            [Paragraph("подпись / дата", s_sign_v)],
        ], colWidths=[sign_col]),
        Table([
            [Paragraph("Менеджер:", s_sign_h)],
            [Paragraph(manager, s_sign_v)],
            [Spacer(1, 8*mm)],
            [HRFlowable(width="100%", thickness=0.5, color=colors.grey)],
            [Paragraph("подпись / дата", s_sign_v)],
        ], colWidths=[sign_col]),
    ]]
    sign_table = Table(sign_data, colWidths=[sign_col+5*mm, sign_col+5*mm])
    sign_table.setStyle(TableStyle([("VALIGN", (0,0), (-1,-1), "TOP")]))
    elements.append(sign_table)

    doc.build(elements)
    return pdf_path


# ─────────────────────────────────────────────
# Генерация и отправка финальной сметы
# ─────────────────────────────────────────────
async def do_generate(source, state: dict):
    fio     = state.get("fio", "")
    address = state.get("address", "")
    extra   = state.get("extra", "")
    r       = get_result(state)
    msg     = source if isinstance(source, types.Message) else source.message
    try:
        await msg.answer(format_result(r, fio, address, extra), parse_mode="HTML")
        pdf_path = build_pdf(r, fio, address, extra)
        file     = FSInputFile(pdf_path)
        await msg.answer_document(file, caption=f"Смета для {fio}" if fio else "Смета")
        os.remove(pdf_path)
    except Exception as e:
        await msg.answer(f"Ошибка при расчёте: {e}")


# ─────────────────────────────────────────────
# ХЕНДЛЕРЫ
# ─────────────────────────────────────────────

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    await message.answer(
        "Приветствую!\nЭто бот для создания сметы на забор.\n\n"
        "/mech — создать смету\n/inf — справка"
    )

@dp.message(Command("inf"))
async def cmd_inf(message: types.Message):
    await message.answer(
        "Типы заборов:\n1: 3D Сетка 1.5м\n2: 3D Сетка 1.8м\n"
        "3: Профлист\n4: Штакетник 1.8м\n5: Штакетник 2м\n\n"
        "Ворота:\n1: Эконом — 21 000 руб.\n2: До 4м — 80 000 руб.\n"
        "3: До 5м — 90 000 руб.\n14: Эконом + авт. — 84 000 руб.\n"
        "24: До 4м + авт. — 143 000 руб.\n34: До 5м + авт. — 153 000 руб.\n\n"
        "Расчёт доставки — индивидуально."
    )

@dp.message(Command("mech"))
async def cmd_mech(message: types.Message):
    user_state[message.from_user.id] = {"step": "perimeter"}
    await message.answer("Введи периметр объекта (в метрах):")


# ── Текстовый обработчик ──
@dp.message(F.text)
async def handle_text(message: types.Message):
    uid   = message.from_user.id
    state = user_state.get(uid, {})
    step  = state.get("step")

    if step == "perimeter":
        try:
            num = int(message.text)
            if num <= 0: raise ValueError
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

    elif step == "fio":
        val = message.text.strip()
        if not val:
            await message.answer("ФИО не может быть пустым:")
            return
        state["fio"]  = val
        state["step"] = "address"
        await message.answer("Введи адрес объекта:")

    elif step == "address":
        val = message.text.strip()
        if not val:
            await message.answer("Адрес не может быть пустым:")
            return
        state["address"] = val
        state["step"]    = "extra"
        await message.answer(
            "Введи дополнительные опции\n(покраска, грунтовка и т.п.)\n\n"
            "Если нет — напиши <b>нет</b> или <b>-</b>",
            parse_mode="HTML"
        )

    elif step == "extra":
        val = message.text.strip()
        state["extra"] = "" if val.lower() in ("нет", "-", "no", "н", ".") else val
        state["step"]  = None
        await show_review(message, state)

    elif step and step.startswith("edit_"):
        field = step[5:]
        val   = message.text.strip()

        # Текстовые поля клиента
        if field == "fio":
            if not val: await message.answer("ФИО не может быть пустым:"); return
            state["fio"] = val
        elif field == "address":
            if not val: await message.answer("Адрес не может быть пустым:"); return
            state["address"] = val
        elif field == "extra":
            state["extra"] = "" if val.lower() in ("нет", "-", "no", "н", ".") else val
        elif field == "per":
            try:
                num = int(val)
                if num <= 0: raise ValueError
                state["per"] = num
                # сбрасываем числовые правки при смене периметра
                state.pop("overrides", None)
            except ValueError:
                await message.answer("Введи целое положительное число:")
                return
        else:
            # Числовые правки (kolvo, zena, zena_kalitka, vorota_zena,
            #                   sheben_zena, dem_zena, montage_zena)
            try:
                num = float(val.replace(" ", "").replace(",", "."))
                if num < 0: raise ValueError
            except ValueError:
                await message.answer("Введи числовое значение (например: 48000):")
                return
            state.setdefault("overrides", {})[field] = num

        state["step"] = None
        await show_review(message, state)

    else:
        await message.answer("Используй /mech для создания сметы.")


# ── Подтверждение ──
@dp.callback_query(lambda c: c.data == "confirm_generate")
async def handle_confirm(callback: types.CallbackQuery):
    await callback.answer()
    await do_generate(callback, user_state.get(callback.from_user.id, {}))

# ── Сброс ручных правок ──
@dp.callback_query(lambda c: c.data == "reset_overrides")
async def handle_reset(callback: types.CallbackQuery):
    uid   = callback.from_user.id
    state = user_state.get(uid, {})
    state.pop("overrides", None)
    await callback.answer("Правки сброшены")
    await show_review(callback, state)


# ── Промпты для текстового редактирования ──
EDIT_PROMPTS = {
    "edit_per":          "Введи новый периметр (в метрах):",
    "edit_fio":          "Введи новое ФИО клиента:",
    "edit_address":      "Введи новый адрес объекта:",
    "edit_extra":        "Введи новые доп. опции (или <b>нет</b>/<b>-</b>):",
    "edit_kolvo":        "Введи новое количество (шт.):",
    "edit_zena":         "Введи новую сумму за забор (руб.):",
    "edit_zena_kalitka": "Введи новую цену калитки (руб.):",
    "edit_vorota_zena":  "Введи новую цену ворот (руб.):",
    "edit_sheben_zena":  "Введи новую сумму за щебень (руб.):",
    "edit_dem_zena":     "Введи новую сумму за демонтаж (руб.):",
    "edit_montage_zena": "Введи новую сумму за монтаж (руб.):",
}

@dp.callback_query(lambda c: c.data in EDIT_PROMPTS)
async def handle_edit_text(callback: types.CallbackQuery):
    uid   = callback.from_user.id
    state = user_state.setdefault(uid, {})
    state["step"] = callback.data   # например "edit_kolvo"
    await callback.answer()
    await callback.message.answer(EDIT_PROMPTS[callback.data], parse_mode="HTML")


# ── Редактирование инлайн-выбором ──

@dp.callback_query(lambda c: c.data == "edit_zab")
async def handle_edit_zab(callback: types.CallbackQuery):
    await callback.answer()
    kb = InlineKeyboardBuilder()
    for cd, txt in [("zab_0","3D Сетка 1.5м"),("zab_1","3D Сетка 1.8м"),
                    ("zab_2","Профлист"),("zab_3","Штакетник 1.8м"),("zab_4","Штакетник 2м")]:
        kb.button(text=txt, callback_data=cd)
    kb.adjust(1)
    await callback.message.answer("Выбери новый тип забора:", reply_markup=kb.as_markup())

@dp.callback_query(lambda c: c.data == "edit_zaz")
async def handle_edit_zaz(callback: types.CallbackQuery):
    await callback.answer()
    kb = InlineKeyboardBuilder()
    for cd, txt in [("zaz_1","2 см"),("zaz_2","2 см двустор."),("zaz_3","4 см"),
                    ("zaz_4","4 см двустор."),("zaz_5","6 см шахм."),("zaz_6","8 см шахм.")]:
        kb.button(text=txt, callback_data=cd)
    kb.adjust(1)
    await callback.message.answer("Выбери новый зазор:", reply_markup=kb.as_markup())

@dp.callback_query(lambda c: c.data == "edit_kal")
async def handle_edit_kal(callback: types.CallbackQuery):
    await callback.answer()
    kb = InlineKeyboardBuilder()
    for cd, txt in [("kal_0","Без калитки"),("kal_1","Калитка без замка"),("kal_2","Калитка с замком")]:
        kb.button(text=txt, callback_data=cd)
    kb.adjust(1)
    await callback.message.answer("Выбери новую калитку:", reply_markup=kb.as_markup())

@dp.callback_query(lambda c: c.data == "edit_vor")
async def handle_edit_vor(callback: types.CallbackQuery):
    await callback.answer()
    kb = InlineKeyboardBuilder()
    for cd, txt in [("vor_0","Без ворот"),("vor_1","Ворота эконом"),("vor_2","Ворота до 4м"),
                    ("vor_3","Ворота до 5м"),("vor_14","Эконом + авт."),
                    ("vor_24","До 4м + авт."),("vor_34","До 5м + авт.")]:
        kb.button(text=txt, callback_data=cd)
    kb.adjust(1)
    await callback.message.answer("Выбери новые ворота:", reply_markup=kb.as_markup())

@dp.callback_query(lambda c: c.data == "edit_dem")
async def handle_edit_dem(callback: types.CallbackQuery):
    await callback.answer()
    kb = InlineKeyboardBuilder()
    kb.button(text="Демонтаж",      callback_data="dem_1")
    kb.button(text="Без демонтажа", callback_data="dem_0")
    kb.adjust(1)
    await callback.message.answer("Выбери вариант демонтажа:", reply_markup=kb.as_markup())


# ── Основные inline-хендлеры (первичное заполнение + редактирование) ──

@dp.callback_query(lambda c: c.data.startswith("zab_"))
async def handle_zab(callback: types.CallbackQuery):
    uid   = callback.from_user.id
    state = user_state.setdefault(uid, {})
    state["zab"] = int(callback.data.split("_")[1])
    if state["zab"] not in [3, 4]:
        state["zaz"] = 1
    # сбрасываем числовые правки при смене типа
    state.pop("overrides", None)
    await callback.answer()
    if "dem" in state:
        await show_review(callback, state)
    else:
        kb = InlineKeyboardBuilder()
        for cd, txt in [("kal_0","🔹 Без калитки"),("kal_1","🔹 Калитка без замка"),("kal_2","🔹 Калитка с замком")]:
            kb.button(text=txt, callback_data=cd)
        kb.adjust(1)
        await callback.message.answer("Выберите калитку:", reply_markup=kb.as_markup())

@dp.callback_query(lambda c: c.data.startswith("kal_"))
async def handle_kal(callback: types.CallbackQuery):
    uid   = callback.from_user.id
    state = user_state.setdefault(uid, {})
    state["kal"] = int(callback.data.split("_")[1])
    await callback.answer()
    if "dem" in state:
        await show_review(callback, state)
    else:
        kb = InlineKeyboardBuilder()
        for cd, txt in [("vor_0","🔹 Без ворот"),("vor_1","🔹 Ворота эконом"),
                        ("vor_2","🔹 Ворота до 4м"),("vor_3","🔹 Ворота до 5м"),
                        ("vor_14","🔹 Эконом + авт."),("vor_24","🔹 До 4м + авт."),("vor_34","🔹 До 5м + авт.")]:
            kb.button(text=txt, callback_data=cd)
        kb.adjust(1)
        await callback.message.answer("Выберите ворота:", reply_markup=kb.as_markup())

@dp.callback_query(lambda c: c.data.startswith("vor_"))
async def handle_vor(callback: types.CallbackQuery):
    uid   = callback.from_user.id
    state = user_state.setdefault(uid, {})
    state["vor"] = int(callback.data.split("_")[1])
    await callback.answer()
    if "dem" in state:
        await show_review(callback, state)
    else:
        if state.get("zab", 0) in [3, 4]:
            kb = InlineKeyboardBuilder()
            for cd, txt in [("zaz_1","🔹 2 см"),("zaz_2","🔹 2 см двустор."),("zaz_3","🔹 4 см"),
                            ("zaz_4","🔹 4 см двустор."),("zaz_5","🔹 6 см шахм."),("zaz_6","🔹 8 см шахм.")]:
                kb.button(text=txt, callback_data=cd)
            kb.adjust(1)
            await callback.message.answer("Выберите зазор штакетника:", reply_markup=kb.as_markup())
        else:
            state["zaz"] = 1
            await ask_demontazh(callback)

@dp.callback_query(lambda c: c.data.startswith("zaz_"))
async def handle_zaz(callback: types.CallbackQuery):
    uid   = callback.from_user.id
    state = user_state.setdefault(uid, {})
    state["zaz"] = int(callback.data.split("_")[1])
    await callback.answer()
    if "dem" in state:
        await show_review(callback, state)
    else:
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
    state["dem"] = int(callback.data.split("_")[1])
    await callback.answer()
    if "fio" in state:
        await show_review(callback, state)
    else:
        state["step"] = "fio"
        await callback.message.answer("Введите ФИО клиента\n(например: Иванов Иван Иванович):")


# ═════════════════════════════════════════════
# SOLO-РЕЖИМ (/solo)
# Флоу: добавляем позиции → "стоп" → fio → address → extra → сводка → PDF
# state["mode"] = "solo"
# state["solo_items"] = [{"name": str, "qty": float, "price": float}, ...]
# state["step"] = "solo_item" | "solo_fio" | "solo_address" | "solo_extra"
# ═════════════════════════════════════════════

SOLO_STOP_WORDS = {"стоп", "stop", "готово", "done", "хватит", "всё", "все"}


def solo_total(items: list) -> float:
    return sum(i["qty"] * i["price"] for i in items)


def format_solo_review(state: dict) -> str:
    items   = state.get("solo_items", [])
    fio     = state.get("solo_fio", "—")
    address = state.get("solo_address", "—")
    extra   = state.get("solo_extra") or "—"

    def money(v): return f"{round(v):,} руб.".replace(",", " ")

    lines = ["📝 <b>Проверьте позиции сметы:</b>\n"]
    for i, item in enumerate(items, 1):
        summa = item["qty"] * item["price"]
        lines.append(
            f"{i}. {item['name']} — "
            f"{item['qty']:g} шт. × {money(item['price'])} = <b>{money(summa)}</b>"
        )
    lines.append(f"\n💰 <b>ИТОГО: {money(solo_total(items))}</b>")
    lines.append("─" * 32)
    lines.append(f"👤 ФИО: <b>{fio}</b>")
    lines.append(f"📍 Адрес: <b>{address}</b>")
    lines.append(f"📋 Доп. опции: <b>{extra}</b>")
    return "\n".join(lines)


def solo_review_keyboard(items: list):
    kb = InlineKeyboardBuilder()
    # Удалить позицию
    for i, item in enumerate(items):
        kb.button(text=f"🗑 Удалить: {item['name'][:20]}", callback_data=f"solo_del_{i}")
    kb.button(text="➕ Добавить ещё позицию",   callback_data="solo_add")
    kb.button(text="✏️ ФИО",                    callback_data="solo_edit_fio")
    kb.button(text="✏️ Адрес",                  callback_data="solo_edit_address")
    kb.button(text="✏️ Доп. опции",             callback_data="solo_edit_extra")
    kb.button(text="✅ Всё верно, создать смету", callback_data="solo_confirm")
    kb.adjust(1)
    return kb.as_markup()


async def show_solo_review(target, state: dict):
    text = format_solo_review(state)
    kb   = solo_review_keyboard(state.get("solo_items", []))
    msg  = target if isinstance(target, types.Message) else target.message
    await msg.answer(text, reply_markup=kb, parse_mode="HTML")


def build_solo_pdf(items: list, fio="", address="", extra="", manager=None) -> str:
    if manager is None:
        manager = MANAGER_NAME

    BASE_DIR  = os.path.dirname(os.path.abspath(__file__))
    font_path = os.path.join(BASE_DIR, "DejaVuSans.ttf")
    font_bold = os.path.join(BASE_DIR, "DejaVuSans-Bold.ttf")
    logo_path = os.path.join(BASE_DIR, "logo.png")
    safe_fio  = (fio or "klient").replace(" ", "_")
    pdf_path  = os.path.join(BASE_DIR, f"smeta_solo_{safe_fio}.pdf")

    if "DejaVu" not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont("DejaVu", font_path))
    if "DejaVuBold" not in pdfmetrics.getRegisteredFontNames() and os.path.exists(font_bold):
        pdfmetrics.registerFont(TTFont("DejaVuBold", font_bold))

    BOLD = "DejaVuBold" if "DejaVuBold" in pdfmetrics.getRegisteredFontNames() else "DejaVu"

    C_DARK   = colors.HexColor("#1a2a3a")
    C_ACCENT = colors.HexColor("#2e86c1")
    C_LIGHT  = colors.HexColor("#eaf4fb")
    C_WHITE  = colors.white
    C_BORDER = colors.HexColor("#aed6f1")

    def S(name, font="DejaVu", size=10, color=colors.black, align="LEFT", leading=None, bold=False):
        return ParagraphStyle(
            name, fontName=BOLD if bold else font,
            fontSize=size, textColor=color,
            alignment={"LEFT": 0, "CENTER": 1, "RIGHT": 2}[align],
            leading=leading or size * 1.4,
        )

    s_title   = S("s_title",  size=20, bold=True, color=C_DARK,  align="CENTER")
    s_sub     = S("s_sub",    size=10, color=colors.grey,         align="CENTER")
    s_info    = S("s_info",   size=10, color=C_DARK)
    s_info_b  = S("s_info_b", size=10, bold=True, color=C_DARK)
    s_th      = S("s_th",     size=10, bold=True, color=C_WHITE,  align="CENTER")
    s_td_l    = S("s_td_l",   size=10, color=C_DARK)
    s_td_r    = S("s_td_r",   size=10, color=C_DARK,              align="RIGHT")
    s_total_l = S("s_tot_l",  size=11, bold=True, color=C_WHITE)
    s_total_r = S("s_tot_r",  size=11, bold=True, color=C_WHITE,  align="RIGHT")
    s_extra   = S("s_extra",  size=9,  color=colors.grey)
    s_sign_h  = S("s_sh",     size=9,  bold=True, color=C_DARK)
    s_sign_v  = S("s_sv",     size=9,  color=colors.grey)

    def money(v): return f"{round(v):,} руб.".replace(",", " ")

    W   = A4[0] - 40 * mm
    doc = SimpleDocTemplate(
        pdf_path, pagesize=A4,
        leftMargin=20*mm, rightMargin=20*mm,
        topMargin=15*mm,  bottomMargin=15*mm,
    )
    elements = []

    if os.path.exists(logo_path):
        logo = Image(logo_path, width=50*mm, height=25*mm)
        logo.hAlign = "LEFT"
        elements += [logo, Spacer(1, 4*mm)]

    date_str = datetime.now().strftime("%d.%m.%Y")
    elements += [
        Paragraph("КОММЕРЧЕСКОЕ ПРЕДЛОЖЕНИЕ / СМЕТА", s_title),
        Paragraph(f"Дата составления: {date_str}", s_sub),
        Spacer(1, 5*mm),
        HRFlowable(width="100%", thickness=1.5, color=C_ACCENT),
        Spacer(1, 4*mm),
    ]

    info_rows = []
    if fio:     info_rows.append([Paragraph("Клиент:",        s_info_b), Paragraph(fio,     s_info)])
    if address: info_rows.append([Paragraph("Адрес объекта:", s_info_b), Paragraph(address, s_info)])
    if info_rows:
        t = Table(info_rows, colWidths=[40*mm, W - 40*mm])
        t.setStyle(TableStyle([
            ("VALIGN",        (0,0), (-1,-1), "TOP"),
            ("BOTTOMPADDING", (0,0), (-1,-1), 3),
        ]))
        elements += [t, Spacer(1, 5*mm)]

    # Таблица позиций
    header = [[
        Paragraph("№",            s_th),
        Paragraph("Наименование", s_th),
        Paragraph("Кол-во",       s_th),
        Paragraph("Цена за ед.",  s_th),
        Paragraph("Сумма",        s_th),
    ]]

    body       = []
    row_styles = []
    for i, item in enumerate(items):
        summa = item["qty"] * item["price"]
        body.append([
            Paragraph(str(i+1),          s_td_l),
            Paragraph(item["name"],       s_td_l),
            Paragraph(f"{item['qty']:g}", s_td_r),
            Paragraph(money(item["price"]), s_td_r),
            Paragraph(money(summa),        s_td_r),
        ])
        row_styles.append(("BACKGROUND", (0, i+1), (-1, i+1), C_LIGHT if i % 2 == 0 else C_WHITE))

    total_row = [[
        Paragraph("",                        s_total_l),
        Paragraph("ИТОГО",                   s_total_l),
        Paragraph("",                        s_total_l),
        Paragraph("",                        s_total_l),
        Paragraph(money(solo_total(items)),  s_total_r),
    ]]

    col_w = [10*mm, W - 10*mm - 20*mm - 35*mm - 40*mm, 20*mm, 35*mm, 40*mm]
    table = Table(header + body + total_row, colWidths=col_w, repeatRows=1)
    table.setStyle(TableStyle([
        ("BACKGROUND",    (0,0),  (-1,0),  C_DARK),
        ("FONTNAME",      (0,0),  (-1,0),  BOLD),
        ("TEXTCOLOR",     (0,0),  (-1,0),  C_WHITE),
        ("ALIGN",         (0,0),  (-1,0),  "CENTER"),
        ("BACKGROUND",    (0,-1), (-1,-1), C_ACCENT),
        ("FONTNAME",      (0,-1), (-1,-1), BOLD),
        ("TEXTCOLOR",     (0,-1), (-1,-1), C_WHITE),
        ("GRID",          (0,0),  (-1,-2), 0.5, C_BORDER),
        ("LINEBELOW",     (0,-1), (-1,-1), 1.5, C_ACCENT),
        ("VALIGN",        (0,0),  (-1,-1), "MIDDLE"),
        ("TOPPADDING",    (0,0),  (-1,-1), 5),
        ("BOTTOMPADDING", (0,0),  (-1,-1), 5),
        ("LEFTPADDING",   (0,0),  (-1,-1), 6),
        ("RIGHTPADDING",  (0,0),  (-1,-1), 6),
        *row_styles,
    ]))
    elements += [table, Spacer(1, 3*mm)]

    if extra:
        elements += [Paragraph(f"Дополнительные опции: {extra}", s_extra), Spacer(1, 3*mm)]

    elements += [
        Paragraph("* Стоимость доставки рассчитывается индивидуально.", s_extra),
        Spacer(1, 8*mm),
        HRFlowable(width="100%", thickness=0.5, color=C_BORDER),
        Spacer(1, 6*mm),
    ]

    sign_col  = W / 2 - 5*mm
    sign_data = [[
        Table([
            [Paragraph("Клиент:", s_sign_h)],
            [Paragraph(fio or "____________________", s_sign_v)],
            [Spacer(1, 8*mm)],
            [HRFlowable(width="100%", thickness=0.5, color=colors.grey)],
            [Paragraph("подпись / дата", s_sign_v)],
        ], colWidths=[sign_col]),
        Table([
            [Paragraph("Менеджер:", s_sign_h)],
            [Paragraph(manager, s_sign_v)],
            [Spacer(1, 8*mm)],
            [HRFlowable(width="100%", thickness=0.5, color=colors.grey)],
            [Paragraph("подпись / дата", s_sign_v)],
        ], colWidths=[sign_col]),
    ]]
    sign_table = Table(sign_data, colWidths=[sign_col+5*mm, sign_col+5*mm])
    sign_table.setStyle(TableStyle([("VALIGN", (0,0), (-1,-1), "TOP")]))
    elements.append(sign_table)

    doc.build(elements)
    return pdf_path


# ── /solo — старт ──
@dp.message(Command("solo"))
async def cmd_solo(message: types.Message):
    user_state[message.from_user.id] = {
        "mode":       "solo",
        "step":       "solo_item",
        "solo_items": [],
    }
    await message.answer(
        "📋 <b>Режим ручной сметы</b>\n\n"
        "Введи позицию в формате:\n"
        "<code>Наименование / количество / цена за единицу</code>\n\n"
        "Например:\n"
        "<code>Профлист С8 / 40 / 320</code>\n"
        "<code>Монтаж / 1 / 15000</code>\n\n"
        "Когда добавишь все позиции — напиши <b>стоп</b>",
        parse_mode="HTML"
    )


# ── solo: добавление позиции через кнопку ──
@dp.callback_query(lambda c: c.data == "solo_add")
async def solo_add(callback: types.CallbackQuery):
    uid   = callback.from_user.id
    state = user_state.setdefault(uid, {})
    state["step"] = "solo_item"
    await callback.answer()
    await callback.message.answer(
        "Введи следующую позицию:\n"
        "<code>Наименование / количество / цена за единицу</code>",
        parse_mode="HTML"
    )


# ── solo: удалить позицию ──
@dp.callback_query(lambda c: c.data.startswith("solo_del_"))
async def solo_del(callback: types.CallbackQuery):
    uid   = callback.from_user.id
    state = user_state.get(uid, {})
    idx   = int(callback.data.split("_")[2])
    items = state.get("solo_items", [])
    if 0 <= idx < len(items):
        removed = items.pop(idx)
        await callback.answer(f"Удалено: {removed['name']}")
    else:
        await callback.answer("Позиция не найдена")
    await show_solo_review(callback, state)


# ── solo: редактирование текстовых полей ──
SOLO_EDIT_PROMPTS = {
    "solo_edit_fio":     "Введи новое ФИО клиента:",
    "solo_edit_address": "Введи новый адрес объекта:",
    "solo_edit_extra":   "Введи новые доп. опции (или <b>нет</b>/<b>-</b>):",
}

@dp.callback_query(lambda c: c.data in SOLO_EDIT_PROMPTS)
async def solo_edit_field(callback: types.CallbackQuery):
    uid   = callback.from_user.id
    state = user_state.setdefault(uid, {})
    state["step"] = callback.data   # "solo_edit_fio" / "solo_edit_address" / "solo_edit_extra"
    await callback.answer()
    await callback.message.answer(SOLO_EDIT_PROMPTS[callback.data], parse_mode="HTML")


# ── solo: подтверждение ──
@dp.callback_query(lambda c: c.data == "solo_confirm")
async def solo_confirm(callback: types.CallbackQuery):
    uid   = callback.from_user.id
    state = user_state.get(uid, {})
    items = state.get("solo_items", [])
    await callback.answer()

    if not items:
        await callback.message.answer("Нет ни одной позиции. Добавь хотя бы одну.")
        return

    fio     = state.get("solo_fio", "")
    address = state.get("solo_address", "")
    extra   = state.get("solo_extra", "")

    def money(v): return f"{round(v):,} руб.".replace(",", " ")

    # Текстовая смета в чат
    lines = ["📋 <b>СМЕТА</b>", f"Дата: {datetime.now().strftime('%d.%m.%Y')}"]
    if fio:     lines.append(f"Клиент: {fio}")
    if address: lines.append(f"Адрес объекта: {address}")
    lines.append("─" * 32)
    for i, item in enumerate(items, 1):
        summa = item["qty"] * item["price"]
        lines.append(f"{i}. {item['name']} — {item['qty']:g} шт. × {money(item['price'])} = {money(summa)}")
    if extra:
        lines.append("─" * 32)
        lines.append(f"Доп. опции: {extra}")
    lines.append("─" * 32)
    lines.append(f"💰 <b>ИТОГО: {money(solo_total(items))}</b>")
    lines.append("+ доставка рассчитывается индивидуально")

    try:
        await callback.message.answer("\n".join(lines), parse_mode="HTML")
        pdf_path = build_solo_pdf(items, fio, address, extra)
        file     = FSInputFile(pdf_path)
        await callback.message.answer_document(file, caption=f"Смета для {fio}" if fio else "Смета")
        os.remove(pdf_path)
    except Exception as e:
        await callback.message.answer(f"Ошибка при генерации: {e}")


# ─────────────────────────────────────────────
async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
