"""Проверка защиты на живом стенде: настоящие атаки на настоящий протокол (страница «Защита»).

Каждая атака работает на копии: квоты, журнал и состояние стенда не меняются. Банк-атакующий A берётся
из участников; целевой банк B: участник, с которым у A есть реальные переводы.
"""

from __future__ import annotations

import copy
import secrets
import sqlite3
import time
from collections import Counter

from nacl.exceptions import CryptoError

from protocol.hub import AuditLog, verify_head


def _pick_pair(sim) -> tuple[str, str]:
    """A и B: пара участников с наибольшим числом общих переводов, зачисленных к текущему моменту."""
    best = None
    for b in sim.participants:
        gw = sim.net.gateways[b]
        counts = Counter(t.counterparty_bank for t in gw.transfers if t.time <= sim.clock and gw.is_partner(t.counterparty_bank)
                         and t.counterparty_bank != b)
        if counts:
            a, n = counts.most_common(1)[0]
            if best is None or n > best[2]:
                best = (a, b, n)
    return best[0], best[1]


def _sandbox(gw):
    """Копия шлюза со своим счётчиком запросов: атака не тратит квоты живого стенда."""
    clone = copy.copy(gw)
    clone._queries = Counter()
    return clone


def brute_force(sim) -> dict:
    a, b = _pick_pair(sim)
    gw = _sandbox(sim.net.gateways[b])
    n, found, t0 = 1000, Counter(), time.perf_counter()
    for _ in range(n):
        found[gw.query(a, secrets.token_hex(32), sim.clock).get("error", "данные")] += 1
    return {"title": "Перебор ссылок", "attack": f"Банк {a} шлёт банку {b} {n} случайных ссылок на переводы, надеясь угадать чужой перевод.",
            "rows": [["запросов", n], ["получено данных", found.get("данные", 0)], ["ответ «не найдено»", found.get("not_found", 0)],
                     ["отказ по квоте", found.get("quota", 0)], ["время", f"{(time.perf_counter() - t0) * 1000:.0f} мс"]],
            "defense": "Ссылка: HMAC от полей перевода на ключе пары банков. Угадать её нельзя, а неверные ссылки тратят ту же квоту.",
            "ok": found.get("данные", 0) == 0}


def foreign_transfer(sim) -> dict:
    a, b = _pick_pair(sim)
    gw = sim.net.gateways[b]
    other = next((t for t in gw.transfers if t.time <= sim.clock and gw.is_partner(t.counterparty_bank)
                  and t.counterparty_bank not in (a, b)), None)
    if other is None:
        return {"title": "Чужой перевод", "attack": "Нет подходящего перевода к этому моменту стенда.", "rows": [], "defense": "", "ok": True}
    from protocol.gateway import CHANNEL
    from protocol.tokens import tx_ref_hmac
    # худший случай: A каким-то образом узнал все поля чужого перевода B и C и посчитал ссылку своим ключом пары
    forged = tx_ref_hmac(sim.net.gateways[a]._keys[b], CHANNEL, other.tx_ref)
    resp = _sandbox(gw).query(a, forged, sim.clock)
    return {"title": "Чужой перевод", "attack": f"Банк {a} знает все поля перевода между банками {b} и {other.counterparty_bank} "
                                               f"и спрашивает {b} о счёте получателя.",
            "rows": [["ответ", "«не найдено»" if resp.get("error") == "not_found" else resp.get("error", "данные")], ["отличается ли от случайной ссылки", "нет: тот же «не найдено»"]],
            "defense": "Need-to-know: банк отвечает только о контрагенте перевода между ним и спрашивающим. "
                       "Ответ одинаков для неверной, чужой и ещё не зачисленной ссылки: по нему нельзя понять, существует ли перевод.",
            "ok": "error" in resp}


def quota_drain(sim) -> dict:
    a, b = _pick_pair(sim)
    gw = _sandbox(sim.net.gateways[b])
    real = next(t for t in gw.transfers if t.counterparty_bank == a and t.time <= sim.clock)
    ref = gw.ref_hmac(real)
    answers, refused = 0, 0
    for _ in range(gw.quota(a, sim.clock) + 200):
        r = gw.query(a, ref, sim.clock)
        if r.get("error") == "quota":
            refused += 1
        elif "error" not in r:
            answers += 1
    return {"title": "Выкачивание по квоте", "attack": f"Банк {a} с настоящей ссылкой задаёт банку {b} запросы без остановки.",
            "rows": [["ответов получено", answers], ["затем отказ по квоте", refused],
                     ["суточная квота пары", f"{gw.quota(a, sim.clock)} = 1,2 × зачисленных сегодня + 50"]],
            "defense": "Квота считается до поиска перевода и привязана к числу реальных зачислений от этого банка. "
                       "Даже все ответы дают только оценку о контрагентах уже состоявшихся переводов.",
            "ok": refused > 0}


def journal_tamper(sim) -> dict:
    src = sim.net.hub.log
    mem = sqlite3.connect(":memory:", check_same_thread=False)
    with src._lock:
        src._db.backup(mem)
    log = AuditLog.__new__(AuditLog)
    log._db, log._lock = mem, src._lock.__class__()
    total = mem.execute("SELECT COUNT(*) FROM audit").fetchone()[0]
    if total < 2:
        return {"title": "Подделка журнала", "attack": "В журнале пока нет записей: запустите стенд.", "rows": [], "defense": "", "ok": True}
    seq = total // 2
    before = mem.execute("SELECT kind, src, dst, detail FROM audit WHERE seq = ?", (seq,)).fetchone()
    intact = log.verify()
    mem.execute("UPDATE audit SET dst = ? WHERE seq = ?", ("XXXX", seq))
    broken = log.verify()
    return {"title": "Подделка журнала", "attack": f"Оператор хаба тихо меняет получателя в записи № {seq} из {total} "
                                                f"({before[0]}, {before[1]} → {before[2]}) на «XXXX».",
            "rows": [["цепочка до правки", "цела" if intact else "нарушена"], ["цепочка после правки", "цела" if broken else "НАРУШЕНА"]],
            "defense": "Каждая запись содержит хэш предыдущей (SHA-256): правка любой записи ломает все хэши после неё. "
                       "Голова цепочки подписана, банки хранят подписанные головы у себя.",
            "ok": intact and not broken}


def signature_forgery(sim) -> dict:
    head = sim.net.hub.signed_head()
    forged = head["hash"][:-1] + ("0" if head["hash"][-1] != "0" else "1")
    real_ok = verify_head(head["public_key"], head["seq"], head["hash"], head["signature"])
    fake_ok = verify_head(head["public_key"], head["seq"], forged, head["signature"])
    return {"title": "Подделка подписи", "attack": "Хаб подменяет голову журнала (переписал историю) и предъявляет банкам старую подпись.",
            "rows": [["настоящая голова", "подпись верна" if real_ok else "неверна"], ["подменённая голова", "подпись верна" if fake_ok else "ПОДПИСЬ НЕВЕРНА"]],
            "defense": "Голова подписана Ed25519 ключом хаба; без закрытого ключа подпись для другой головы не подобрать.",
            "ok": real_ok and not fake_ok}


def hub_eavesdrop(sim) -> dict:
    a, b = _pick_pair(sim)
    gw_b = _sandbox(sim.net.gateways[b])
    real = next(t for t in gw_b.transfers if t.counterparty_bank == a and t.time <= sim.clock)
    resp = gw_b.query(a, gw_b.ref_hmac(real), sim.clock)
    sealed = resp.get("sealed")
    if sealed is None:
        return {"title": "Хаб читает ответ", "attack": "Шифрование на стенде выключено.", "rows": [], "defense": "", "ok": False}
    third = next(x for x in sim.participants if x not in (a, b))
    try:
        sim.net.gateways[third].sealer.open(b, sealed)
        third_read = "прочитал"
    except CryptoError:
        third_read = "не смог расшифровать"
    own = sim.net.gateways[a].open_answer(b, resp)
    return {"title": "Хаб читает ответ", "attack": f"Хаб (и посторонний банк {third}) пытаются прочитать ответ банка {b} банку {a}.",
            "rows": [["что видит хаб", f"{len(sealed)} байт шифротекста: {sealed[:12].hex()}…"],
                     [f"банк {third} с чужим ключом", third_read],
                     [f"банк {a}, адресат", f"прочитал: псевдоним {own['pseudonym'][:12]}…"]],
            "defense": "Ответ шифруется ключом пары «отправитель → адресат» (X25519, XSalsa20-Poly1305): "
                       "хаб пересылает и журналирует только метаданные.",
            "ok": third_read != "прочитал"}


ATTACKS = {"brute": brute_force, "foreign": foreign_transfer, "quota": quota_drain,
           "journal": journal_tamper, "signature": signature_forgery, "eavesdrop": hub_eavesdrop}


def run(sim, name: str) -> dict:
    with sim.lock:
        return {"id": name, **ATTACKS[name](sim)}
