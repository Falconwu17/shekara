"""Резюме проекта для заявки (раздел 8 положения): по одной странице A4 на русском и английском.

Тексты резюме живут здесь, а не собираются из аннотации: у резюме жёсткий лимит в одну страницу.
Результат — resume_ru.docx и resume_en.docx в docs/submission/ (в git не попадают, см. .gitignore).

    .venv\\Scripts\\python -m pip install python-docx
    .venv\\Scripts\\python -m docs.submission.resume
"""

from __future__ import annotations

import sys
from pathlib import Path

from docx import Document
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

HERE = Path(__file__).resolve().parent
STAND = "https://shekara.21restobar.kz"
REPO = "https://github.com/Falconwu17/shekara"
FONT = "Times New Roman"
GREY = RGBColor(0x55, 0x55, 0x55)

RU = {
    "file": "resume_ru.docx",
    "title": "РЕЗЮМЕ ПРОЕКТА",
    "name": "ШЕКАРА: межбанковское выявление сетей дропов без обмена клиентскими данными",
    "meta": [
        ("Конкурс", "Республиканский конкурс Ассоциации банков РК 2026; треки AI for Finance (антифрод) и Digital Banking (AML/regtech)"),
        ("Участник", "Джусупов Сагир Едгеевич (индивидуальное участие)"),
        ("Научный руководитель", "Касымова Акмарал Хамзиевна"),
        ("Организация", "НАО «Западно-Казахстанский аграрно-технический университет имени Жангир хана», "
                        "090009, Республика Казахстан, г. Уральск, ул. Жангир хана, 51"),
        ("Прототип и код", f"{STAND} · {REPO}"),
    ],
    "sections": [
        ("Проблема", "В 2024 году в Казахстане зарегистрировано более 17 тыс. случаев интернет-мошенничества; "
         "жертвы потеряли около 45 млрд ₸, вернуть удалось около 2 млрд ₸. Похищенные деньги проходят через "
         "цепочку счетов-дропов в разных банках. Каждый банк видит лишь одно звено и не распознаёт схему, "
         "а объединить данные банков не позволяют банковская тайна и закон о персональных данных."),
        ("Решение", "ШЕКАРА состоит из протокола обмена и графовой модели. Клиентские записи не покидают банк: "
         "банк сообщает соседу только оценку риска счёта и только по ссылке на реальный зачисленный перевод "
         "между их клиентами. Банк-получатель проверяет входящее зачисление за доли миллисекунды, подозрительная "
         "цепочка трассируется дальше по зачисленным переводам. Каждый банк видит свой сегмент, полную цепочку "
         "собирает только оператор с мандатом (Антифрод-центр НБК). Обращения записываются в журнал на "
         "хэш-цепочке, уровень раскрытия (L1, L2, L3) выбирает регулятор."),
        ("Данные и метод", "Открытый набор IBM AMLworld (NeurIPS 2023): 5,08 млн переводов, 30 банков-участников. "
         "Модель каждого банка: градиентный бустинг на признаках его графа переводов, оценки соседей входят как "
         "признаки внешних узлов. Сравнение с изолированным банком, общим графом и центральным оператором, "
         "который видит только межбанковские потоки (подход MITS); 3 запуска, парный бутстреп по паттернам."),
    ],
    "results_head": "Результаты",
    "results": [
        "Модель на графе самого банка значимо сильнее центрального оператора: PR-AUC 0,315–0,354 против 0,162, "
        "F1 +0,18 (значимо в 3 из 3 запусков).",
        "Обмен оценками поднимает PR-AUC с 0,315 до 0,354 (+13%) во всех запусках; прирост пока статистически не значим.",
        "Цена приватности измерена: один зашумлённый балл (L1) не помогает, польза появляется при векторном выпуске (L2, L3).",
        "Проверка при зачислении занимает 0,7 мс; с временной задержкой исходящих для верхушки очереди деньги "
        "остаются на счёте в 30% звеньев отмывания.",
    ],
    "tail": ("Готовность и внедрение", "TRL 4. Работает прототип: шлюзы банков, хаб с журналом, трассировка "
             "инцидентов, кабинет оператора; код открыт, 22 автоматические проверки пройдены. Следующий шаг: "
             "пилот с двумя банками в регуляторной песочнице НБК и АРРФР."),
    "signatures": [("Научный руководитель", "Касымова А. Х."), ("Участник", "Джусупов С. Е.")],
    "seal": "М. П.",
    "date": "«____» _____________ 2026 г.",
}

EN = {
    "file": "resume_en.docx",
    "title": "PROJECT SUMMARY",
    "name": "SHEKARA: cross-bank detection of money-mule networks without pooling customer data",
    "meta": [
        ("Contest", "Republican contest of the Association of Banks of Kazakhstan 2026; tracks AI for Finance "
                    "(anti-fraud) and Digital Banking (AML/regtech)"),
        ("Participant", "Sagir Dzhussupov (individual entry)"),
        ("Supervisor", "Akmaral Kassymova"),
        ("Institution", "Zhangir Khan West Kazakhstan Agrarian and Technical University, "
                        "51 Zhangir Khan St., Uralsk 090009, Republic of Kazakhstan"),
        ("Prototype and code", f"{STAND} · {REPO}"),
    ],
    "sections": [
        ("Problem", "In 2024 Kazakhstan registered more than 17,000 cases of online fraud; victims lost about "
         "KZT 45 billion and only about KZT 2 billion was recovered. Stolen money moves through a chain of mule "
         "accounts at different banks. Each bank sees a single link and cannot recognise the scheme, while "
         "banking secrecy and personal-data law rule out pooling the banks' data."),
        ("Solution", "SHEKARA is an exchange protocol plus a graph model. Customer records never leave a bank: "
         "a bank tells a counterparty only a risk estimate for an account, and only by quoting a real credited "
         "transfer between their customers. The receiving bank checks an incoming credit in under a millisecond, "
         "and a suspicious chain is traced onward along credited transfers. Each bank sees its own segment; only "
         "a mandated operator (the NBK anti-fraud centre) assembles the full chain. Every request is written to a "
         "hash-chained log, and the regulator chooses the disclosure level (L1, L2, L3)."),
        ("Data and method", "Open IBM AMLworld dataset (NeurIPS 2023): 5.08 million transfers, 30 participating "
         "banks. Each bank runs gradient boosting on features of its own transfer graph; counterparties' "
         "estimates enter as features of external nodes. Compared with an isolated bank, a pooled graph and a "
         "central operator that sees only interbank flows (the MITS approach); 3 runs, paired bootstrap over patterns."),
    ],
    "results_head": "Results",
    "results": [
        "A model on the bank's own graph significantly beats the central operator: PR-AUC 0.315–0.354 versus "
        "0.162, F1 +0.18 (significant in 3 of 3 runs).",
        "Exchanging estimates raises PR-AUC from 0.315 to 0.354 (+13%) in every run; the gain is not yet statistically significant.",
        "The price of privacy is measured: a single noisy score (L1) does not help; benefit appears with vector releases (L2, L3).",
        "The incoming-credit check takes 0.7 ms; a temporary hold on outgoing payments for the top of the queue "
        "keeps money in place on 30% of laundering hops.",
    ],
    "tail": ("Readiness and roll-out", "TRL 4. Working prototype: bank gateways, a logged hub, incident tracing "
             "and an operator view; open-source code, 22 automated checks passing. Next step: a two-bank pilot in "
             "the NBK and ARDFM regulatory sandbox."),
    "signatures": [("Supervisor", "A. Kassymova"), ("Participant", "S. Dzhussupov")],
    "seal": "Seal",
    "date": "Date: ____ ______________ 2026",
}


def _font(run, size=11, bold=False, italic=False, color=None):
    run.font.name = FONT
    run._element.rPr.rFonts.set(qn("w:eastAsia"), FONT)
    run.font.size, run.bold, run.italic = Pt(size), bold, italic
    if color is not None:
        run.font.color.rgb = color
    return run


def _para(doc_or_cell, text="", *, size=11, bold=False, align=WD_ALIGN_PARAGRAPH.JUSTIFY, before=0, after=4):
    par = doc_or_cell.add_paragraph()
    par.alignment = align
    fmt = par.paragraph_format
    fmt.space_before, fmt.space_after, fmt.line_spacing = Pt(before), Pt(after), 1.0
    if text:
        _font(par.add_run(text), size, bold)
    return par


def _no_borders(table):
    borders = OxmlElement("w:tblBorders")
    for edge in ("top", "left", "bottom", "right", "insideH", "insideV"):
        el = OxmlElement(f"w:{edge}")
        el.set(qn("w:val"), "nil")
        borders.append(el)
    table._tbl.tblPr.append(borders)


def _rule(par):
    """Тонкая линия под абзацем — отделяет шапку от текста."""
    border = OxmlElement("w:pBdr")
    bottom = OxmlElement("w:bottom")
    for key, value in (("w:val", "single"), ("w:sz", "6"), ("w:space", "4"), ("w:color", "808080")):
        bottom.set(qn(key), value)
    border.append(bottom)
    par._p.get_or_add_pPr().append(border)


def _fixed_widths(table, widths):
    """Ширины колонок, которые Word не пересчитывает по содержимому."""
    table.autofit = False
    layout = OxmlElement("w:tblLayout")
    layout.set(qn("w:type"), "fixed")
    table._tbl.tblPr.append(layout)
    for row in table.rows:
        for cell, width in zip(row.cells, widths):
            cell.width = width


def build(spec: dict) -> Path:
    doc = Document()
    section = doc.sections[0]
    section.page_width, section.page_height = Cm(21), Cm(29.7)
    section.top_margin, section.bottom_margin = Cm(1.5), Cm(1.5)
    section.left_margin, section.right_margin = Cm(2.0), Cm(1.5)
    doc.styles["Normal"].font.name = FONT
    doc.styles["Normal"].font.size = Pt(11)

    _para(doc, spec["title"], size=14, bold=True, align=WD_ALIGN_PARAGRAPH.CENTER, after=2)
    _para(doc, spec["name"], size=12, bold=True, align=WD_ALIGN_PARAGRAPH.CENTER, after=8)

    meta = doc.add_table(rows=0, cols=2)
    _no_borders(meta)
    meta.alignment = WD_TABLE_ALIGNMENT.CENTER
    for label, value in spec["meta"]:
        row = meta.add_row().cells
        for cell, text, bold in ((row[0], label, True), (row[1], value, False)):
            cell.paragraphs[0].paragraph_format.space_after = Pt(1)
            _font(cell.paragraphs[0].add_run(text), 10.5, bold)
    _fixed_widths(meta, (Cm(4.6), Cm(12.9)))
    _rule(_para(doc, after=6))

    for head, text in spec["sections"]:
        par = _para(doc, after=5)
        _font(par.add_run(f"{head}. "), bold=True)
        _font(par.add_run(text))

    _para(doc, spec["results_head"] + ".", bold=True, after=2)
    for item in spec["results"]:
        par = _para(doc, after=2, align=WD_ALIGN_PARAGRAPH.LEFT)
        fmt = par.paragraph_format
        fmt.left_indent, fmt.first_line_indent = Cm(0.5), Cm(-0.5)
        fmt.tab_stops.add_tab_stop(Cm(0.5))
        _font(par.add_run("•	" + item))
    par = _para(doc, before=3, after=5)
    _font(par.add_run(f"{spec['tail'][0]}. "), bold=True)
    _font(par.add_run(spec["tail"][1]))

    signs = doc.add_table(rows=0, cols=2)
    _no_borders(signs)
    for role, name in spec["signatures"]:
        row = signs.add_row().cells
        _font(row[0].paragraphs[0].add_run(f"{role}:"))
        _font(row[1].paragraphs[0].add_run(f"____________________  / {name} /"))
        for cell in row:
            cell.paragraphs[0].paragraph_format.space_before = Pt(10)
    row = signs.add_row().cells
    _font(row[0].paragraphs[0].add_run(spec["seal"]))
    _font(row[1].paragraphs[0].add_run(spec["date"]))
    for cell in row:
        cell.paragraphs[0].paragraph_format.space_before = Pt(10)
    _fixed_widths(signs, (Cm(5.0), Cm(12.5)))
    # Word требует абзац после таблицы — делаем его минимальным, чтобы не создавал вторую страницу
    last = doc.paragraphs[-1] if doc.paragraphs[-1]._p is doc.element.body[-2] else _para(doc)
    last.paragraph_format.space_after = Pt(0)
    for run in last.runs:
        run.font.size = Pt(1)

    out = HERE / spec["file"]
    doc.save(out)
    return out


def main() -> int:
    for spec in (RU, EN):
        print(build(spec))
    return 0


if __name__ == "__main__":
    sys.exit(main())
