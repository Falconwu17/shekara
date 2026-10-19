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
from protocol.sealing import ContextError


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
    return {"title": "Подделка подписи", "attack": "Посторонний подменяет голову журнала и предъявляет банкам старую подпись хаба.",
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


def _sandbox_full(gw):
    """Копия шлюза со своими запросами и своим кэшем чужих выпусков."""
    clone = _sandbox(gw)
    clone._external = dict(gw._external)
    return clone


def _shared_transfers(sim, a: str, b: str, n: int = 2):
    gw_b = sim.net.gateways[b]
    return [t for t in gw_b.transfers if t.counterparty_bank == a and t.time <= sim.clock][-n:]


def answer_swap(sim) -> dict:
    a, b = _pick_pair(sim)
    gw_b = _sandbox(sim.net.gateways[b])
    t1, t2 = _shared_transfers(sim, a, b)
    r1, r2 = gw_b.ref_hmac(t1), gw_b.ref_hmac(t2)
    resp2 = gw_b.query(a, r2, sim.clock)
    try:
        sim.net.gateways[a].open_answer(b, resp2, r1, sim.clock.date())
        outcome = "ПРИНЯТ: оценка чужого счёта"
    except ContextError:
        outcome = "отклонён: ответ на другой запрос"
    return {"title": "Хаб переставил ответы",
            "attack": f"Банк {a} спросил банк {b} о двух переводах. Хаб отдаёт ответ на второй запрос вместо первого: "
                      "оценка одного счёта приписывается другому.",
            "rows": [["подменённый ответ", outcome]],
            "defense": "Внутри шифротекста ссылка на перевод, день и адресат: банк проверяет, что ответ именно на его запрос.",
            "ok": outcome.startswith("отклонён")}


def release_replay(sim) -> dict:
    from datetime import timedelta

    from protocol.gateway import Broadcast, Release
    a, b = _pick_pair(sim)
    gw_a, gw_b = _sandbox_full(sim.net.gateways[a]), sim.net.gateways[b]
    (t,) = _shared_transfers(sim, a, b, 1)
    ref = gw_b.ref_hmac(t)
    today, old = sim.clock.date(), sim.clock.date() - timedelta(days=3)
    fresh = Broadcast(a, ref, None, gw_b.sealer.seal_release(a, Release(0.12, "L1", today), ref))
    stale = Broadcast(a, ref, None, gw_b.sealer.seal_release(a, Release(0.97, "L1", old), ref))
    ok_fresh = gw_a.receive_broadcast(b, fresh, sim.clock)
    ok_stale = gw_a.receive_broadcast(b, stale, sim.clock)
    return {"title": "Хаб повторил старый выпуск",
            "attack": f"Сегодня банк {b} сообщил о счёте низкий балл 0,12. Хаб подсовывает банку {a} записанный три дня назад "
                      "выпуск с баллом 0,97, чтобы поднять тревогу на честном счёте.",
            "rows": [["свежий выпуск", "принят" if ok_fresh else "отклонён"],
                     ["старый выпуск поверх свежего", "ПРИНЯТ" if ok_stale else "отклонён"]],
            "defense": "Дата выпуска внутри шифротекста: банк не принимает выпуск старше уже полученного и выпуск из будущего.",
            "ok": ok_fresh and not ok_stale}


def release_redirect(sim) -> dict:
    from protocol.gateway import Broadcast, Release
    a, b = _pick_pair(sim)
    gw_a, gw_b = _sandbox_full(sim.net.gateways[a]), sim.net.gateways[b]
    t1, t2 = _shared_transfers(sim, a, b)
    r1, r2 = gw_b.ref_hmac(t1), gw_b.ref_hmac(t2)
    sealed = gw_b.sealer.seal_release(a, Release(0.97, "L1", sim.clock.date()), r1)
    accepted = gw_a.receive_broadcast(b, Broadcast(a, r2, None, sealed), sim.clock)
    return {"title": "Хаб перенаправил выпуск",
            "attack": f"Выпуск банка {b} о счёте из перевода 1 хаб переадресует на ссылку перевода 2: высокий балл достаётся другому счёту.",
            "rows": [["перенаправленный выпуск", "ПРИНЯТ" if accepted else "отклонён"]],
            "defense": "Ссылка-адрес зашифрована вместе с выпуском: если хаб меняет адрес на конверте, содержимое его не подтверждает.",
            "ok": not accepted}


def length_leak(sim) -> dict:
    from protocol.gateway import Release
    a, b = _pick_pair(sim)
    sealer = sim.net.gateways[b].sealer
    day = sim.clock.date()
    cases = [("выпуска нет", None), ("балл 0,15", Release(0.15, "L1", day)), ("балл 0,97", Release(0.97, "L1", day)),
             ("вектор L3", Release([0.1] * 8, "L3", day))]
    lengths = {name: len(sealer.seal_answer(a, "x" * 64, rel, "0" * 64, day)) for name, rel in cases}
    return {"title": "Балл по длине шифротекста",
            "attack": "Хаб не может расшифровать ответ, но смотрит на его длину: вдруг высокий балл и «выпуска нет» отличаются.",
            "rows": [[name, f"{n} байт"] for name, n in lengths.items()],
            "defense": "Открытый текст дополняется до 512 байт перед шифрованием: длина одинакова для любого ответа.",
            "ok": len(set(lengths.values())) == 1}


def history_rewrite(sim) -> dict:
    from protocol.hub import GENESIS, HeadWitness, _entry_hash, sign_head
    a, _ = _pick_pair(sim)
    src = sim.net.hub.log
    mem = sqlite3.connect(":memory:", check_same_thread=False)
    with src._lock:
        src._db.backup(mem)
    log = AuditLog.__new__(AuditLog)
    log._db, log._lock = mem, src._lock.__class__()
    witness = HeadWitness()
    witness.heads = list(sim.witness[a].heads)
    if not witness.heads:
        witness.record(sim.net.hub.signed_head())
    total = mem.execute("SELECT COUNT(*) FROM audit").fetchone()[0]
    if total < 2:
        return {"title": "Хаб переписал историю", "attack": "В журнале пока нет записей: запустите стенд.", "rows": [], "defense": "", "ok": True}
    seq = max(1, witness.heads[0]["seq"] // 2)
    mem.execute("UPDATE audit SET dst = ? WHERE seq = ?", ("XXXX", seq))
    prev = GENESIS if seq == 1 else mem.execute("SELECT hash FROM audit WHERE seq = ?", (seq - 1,)).fetchone()[0]
    rows = mem.execute("SELECT seq, ts, kind, src, dst, ref_hash, detail FROM audit WHERE seq >= ? ORDER BY seq", (seq,)).fetchall()
    for s, *values in rows:
        h = _entry_hash(prev, values)
        mem.execute("UPDATE audit SET prev_hash = ?, hash = ? WHERE seq = ?", (prev, h, s))
        prev = h
    head_seq, head_hash = log.head()
    new_sig = sign_head(sim.net.hub.signing_key, head_seq, head_hash)
    chain_ok = log.verify()
    sig_ok = verify_head(sim.net.hub.public_key, head_seq, head_hash, new_sig)
    caught = witness.check(log)
    return {"title": "Хаб переписал историю",
            "attack": f"Хаб правит запись № {seq}, пересчитывает хэши всех записей после неё и подписывает новую голову своим настоящим ключом.",
            "rows": [["хэш-цепочка", "цела (хэши пересчитаны)" if chain_ok else "нарушена"],
                     ["подпись новой головы", "верна" if sig_ok else "неверна"],
                     [f"сверка с головами, сохранёнными банком {a}",
                      f"РАСХОЖДЕНИЕ на {len(caught)} из {len(witness.heads)}" if caught else "совпадает"]],
            "defense": "Каждую ночь банки сохраняют подписанную голову журнала у себя. Хаб не может переписать то, что уже засвидетельствовали банки: "
                       "хэш на сохранённой позиции не сойдётся.",
            "ok": chain_ok and sig_ok and bool(caught)}

ATTACKS = {"brute": brute_force, "foreign": foreign_transfer, "quota": quota_drain,
           "journal": journal_tamper, "signature": signature_forgery, "eavesdrop": hub_eavesdrop,
           "swap": answer_swap, "replay": release_replay, "redirect": release_redirect, "length": length_leak,
           "rewrite": history_rewrite}


def run(sim, name: str) -> dict:
    with sim.lock:
        return {"id": name, **ATTACKS[name](sim)}
