// Форматирование чисел, дат и сумм для интерфейса (русская локаль).

const CURRENCY_CODES = {
  "US Dollar": "USD", "Euro": "EUR", "Yuan": "CNY", "Rupee": "INR", "UK Pound": "GBP", "Yen": "JPY",
  "Swiss Franc": "CHF", "Australian Dollar": "AUD", "Canadian Dollar": "CAD", "Ruble": "RUB",
  "Mexican Peso": "MXN", "Brazil Real": "BRL", "Saudi Riyal": "SAR", "Shekel": "ILS", "Bitcoin": "BTC",
};

export const integer = value => (value == null ? "—" : Number(value).toLocaleString("ru-RU"));

export const decimal = (value, digits = 3) =>
  value == null || Number.isNaN(Number(value)) ? "—" : Number(value).toFixed(digits).replace(".", ",");

export const percent = (value, digits = 0) =>
  value == null || Number.isNaN(Number(value)) ? "—" : `${(value * 100).toFixed(digits).replace(".", ",")}%`;

/** "2022-09-08T10:15:00" → "09.08 10:15" (месяц.день — как в журналах AMLworld) */
export const shortTime = iso => (iso ? iso.slice(5, 16).replace("T", " ").replace("-", ".") : "—");

/** "2022-09-08T10:15:00" → "08.09 10:15" */
export const clockTime = iso => (iso ? `${iso.slice(8, 10)}.${iso.slice(5, 7)} ${iso.slice(11, 16)}` : "—");

export const amount = (value, currency) => {
  const number = Number(value).toLocaleString("ru-RU", { maximumFractionDigits: 2 });
  return currency ? `${number} ${CURRENCY_CODES[currency] || currency}` : number;
};
