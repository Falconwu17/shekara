"""Payment Format AMLworld → казахстанские каналы, только для отображения (раздел 3.3)."""

CHANNEL_MAP = {
    "Wire": "межбанковский перевод по реквизитам (IBAN)",
    "ACH": "межбанковский перевод по реквизитам (клиринг)",
    "Cheque": "межбанковский перевод по реквизитам (клиринг)",
    "Credit Card": "P2P по карте",
    "Cash": "снятие наличных",
    "Bitcoin": "вывод в криптовалюту",
    "Reinvestment": "внутренняя операция",
}


def channel_label(payment_format: str) -> str:
    return CHANNEL_MAP.get(payment_format, payment_format)
