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

from protocol.hub import SIG_TAG, AuditLog, HeadWitness, sign_head, unconfirmed_requests, verify_head
from protocol.sealing import ContextError, ref_digest


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


def _ask(sim, a: str, gw_b, ref: str) -> dict:
    """Запрос банка a банку B так, как его делает шлюз a: с подписью a (без неё B не отвечает)."""
    return gw_b.query(a, ref, sim.clock, sim.net.gateways[a].sign_query(gw_b.bank_id, ref, sim.clock))


def brute_force(sim) -> dict:
    a, b = _pick_pair(sim)
    gw = _sandbox(sim.net.gateways[b])
    n, found, t0 = 1000, Counter(), time.perf_counter()
    for _ in range(n):
        found[_ask(sim, a, gw, secrets.token_hex(32)).get("error", "данные")] += 1
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
    gw_a, day = sim.net.gateways[a], sim.clock.date()
    forged = tx_ref_hmac(gw_a._keys[b], CHANNEL, other.tx_ref)
    random_ref = secrets.token_hex(32)
    # каждый запрос на своей копии со свежей квотой: отказ по квоте не выдаётся за успех защиты
    resp_f, resp_r = _ask(sim, a, _sandbox(gw), forged), _ask(sim, a, _sandbox(gw), random_ref)
    open_f = gw_a.open_answer(b, resp_f, forged, day)  # что видит сам A после расшифровки
    open_r = gw_a.open_answer(b, resp_r, random_ref, day)
    len_f, len_r = len(resp_f.get("sealed", b"")), len(resp_r.get("sealed", b""))
    same = open_f == open_r and resp_f.get("error") == resp_r.get("error") and len_f == len_r

    def shown(r):
        return {"not_found": "«не найдено»", "quota": "отказ по квоте"}.get(r.get("error"), "ДАННЫЕ")

    return {"title": "Чужой перевод", "attack": f"Банк {a} знает все поля перевода между банками {b} и {other.counterparty_bank} "
                                               f"и спрашивает {b} о счёте получателя. Для сравнения тот же запрос со случайной ссылкой.",
            "rows": [["ответ на чужую ссылку", shown(open_f)], ["ответ на случайную ссылку", shown(open_r)],
                     ["шифротекст ответа", f"{len_f} и {len_r} байт"],
                     ["отличается ли от случайной ссылки", "нет: статус, содержимое и длина совпадают" if same else "ДА: ответы различимы"]],
            "defense": "Need-to-know: банк отвечает только о контрагенте перевода между ним и спрашивающим. "
                       "Ответ одинаков для неверной, чужой и ещё не зачисленной ссылки: по нему нельзя понять, существует ли перевод.",
            "ok": open_f.get("error") == "not_found" and same}


def quota_drain(sim) -> dict:
    a, b = _pick_pair(sim)
    gw = _sandbox(sim.net.gateways[b])
    real = next(t for t in gw.transfers if t.counterparty_bank == a and t.time <= sim.clock)
    ref = gw.ref_hmac(real)
    answers, refused = 0, 0
    for _ in range(gw.quota(a, sim.clock) + 200):
        r = _ask(sim, a, gw, ref)
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


def _journal_copy(sim) -> tuple[AuditLog, sqlite3.Connection, bool]:
    """Копия журнала хаба в памяти. На только что сброшенном стенде журнал пуст:
    тогда в копию пишутся настоящие обращения пары банков, чтобы атаке было что править."""
    src = sim.net.hub.log
    mem = sqlite3.connect(":memory:", check_same_thread=False)
    with src._lock:
        src._db.backup(mem)
    log = AuditLog.__new__(AuditLog)
    log._db, log._lock = mem, src._lock.__class__()
    filled = mem.execute("SELECT COUNT(*) FROM audit").fetchone()[0] < 4
    if filled:
        a, b = _pick_pair(sim)
        gw_b = _sandbox(sim.net.gateways[b])
        for tr in _shared_transfers(sim, a, b, 6):
            ref = gw_b.ref_hmac(tr)
            sig = sim.net.gateways[a].sign_query(b, ref, sim.clock)
            answer = gw_b.query(a, ref, sim.clock, sig)
            log.append(sim.clock, "query", a, b, ref_digest(ref), answer.get("error", "ok") + SIG_TAG + sig)
    return log, mem, filled


def _clone_log(log: AuditLog) -> tuple[AuditLog, sqlite3.Connection]:
    mem = sqlite3.connect(":memory:", check_same_thread=False)
    log._db.backup(mem)
    clone = AuditLog.__new__(AuditLog)
    clone._db, clone._lock = mem, log._lock.__class__()
    return clone, mem


def journal_tamper(sim) -> dict:
    log, mem, filled = _journal_copy(sim)
    total = mem.execute("SELECT COUNT(*) FROM audit").fetchone()[0]
    seq = total // 2
    before = mem.execute("SELECT kind, src, dst, detail FROM audit WHERE seq = ?", (seq,)).fetchone()
    intact = log.verify()
    mem.execute("UPDATE audit SET dst = ? WHERE seq = ?", ("XXXX", seq))
    broken = log.verify()
    return {"title": "Подделка журнала", "attack": f"Оператор хаба тихо меняет получателя в записи № {seq} из {total} "
                                                f"({before[0]}, {before[1]} → {before[2]}) на «XXXX»."
                                                + (" Стенд только что сброшен: журнал копии заполнен настоящими запросами пары банков." if filled else ""),
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
    ref = gw_b.ref_hmac(real)
    resp = _ask(sim, a, gw_b, ref)
    sealed = resp.get("sealed")
    if sealed is None:
        return {"title": "Хаб читает ответ", "attack": "Шифрование на стенде выключено.", "rows": [], "defense": "", "ok": False}
    third = next(x for x in sim.participants if x not in (a, b))
    try:
        sim.net.gateways[third].sealer.open(b, sealed)
        third_read = "прочитал"
    except CryptoError:
        third_read = "не смог расшифровать"
    own = sim.net.gateways[a].open_answer(b, resp, ref, sim.clock.date())
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
    resp2 = _ask(sim, a, gw_b, r2)
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
            "defense": "Дата выпуска внутри шифротекста: банк не принимает выпуск старше уже полученного, старше двух ночей "
                       "и выпуск из будущего. Обратный порядок (свежий удержан, отдан старый) проверяет атака «Хаб удержал свежий выпуск».",
            "ok": ok_fresh and not ok_stale}


def release_withhold(sim) -> dict:
    from datetime import timedelta

    from protocol.gateway import MAX_RELEASE_AGE, Broadcast, Release
    a, b = _pick_pair(sim)
    gw_a, gw_b = _sandbox_full(sim.net.gateways[a]), sim.net.gateways[b]
    gw_a._received, gw_a.receipts_seen, gw_a.shortfalls = Counter(), {}, {}
    (t,) = _shared_transfers(sim, a, b, 1)
    ref, now, today = gw_b.ref_hmac(t), sim.clock, sim.clock.date()
    account = gw_a.resolve(b, ref, now).counterparty_account
    gw_a._external.pop((b, account), None)  # свежего выпуска у A нет: хаб его удержал

    def sealed(day):
        return Broadcast(a, ref, None, gw_b.sealer.seal_release(a, Release(0.97, "L1", day), ref))

    ok_old = gw_a.receive_broadcast(b, sealed(today - timedelta(days=3)), now)
    ok_yday = gw_a.receive_broadcast(b, sealed(today - timedelta(days=1)), now)
    in_features_later = gw_a.external_release(b, account, now + MAX_RELEASE_AGE) is not None
    # этой ночью B выпустил для A один выпуск (сегодняшний, балл 0,12) и запечатал квитанцию «для тебя 1»
    check = gw_a.accept_receipt(b, gw_b.sealer.seal_receipt(a, today, 1), now)
    return {"title": "Хаб удержал свежий выпуск, отдал старый",
            "attack": f"Сегодня банк {b} сообщил о счёте низкий балл 0,12. Хаб не доставляет его банку {a}, а отдаёт записанный раньше "
                      "выпуск с баллом 0,97: сначала трёхдневный, затем вчерашний. Свежего выпуска у адресата нет, сравнивать даты не с чем.",
            "rows": [["выпуск трёхдневной давности", "ПРИНЯТ" if ok_old else "отклонён: старше двух ночей"],
                     ["вчерашний выпуск вместо сегодняшнего", "принят: в пределах срока" if ok_yday else "отклонён"],
                     ["вчерашний выпуск через две ночи", "ВСЁ ЕЩЁ В ПРИЗНАКАХ" if in_features_later else "в признаки не читается"],
                     [f"квитанция банка {b} за ночь", f"выпусков для {a}: {check['expected']}, дошло: {check['received']}"],
                     ["недоставка", f"ВИДНА: не хватает {check['missing']}" if check["missing"] else "не видна"]],
            "defense": "Срок жизни выпуска две ночи: более старый не принимается и не читается в признаки. Каждую ночь ответчик "
                       "запечатывает адресату квитанцию «выпусков для тебя N» (хаб не подделает её и не выдаст вчерашнюю за сегодняшнюю); "
                       "адресат сверяет с числом полученных, недостача видна и уходит в разбор с регулятором. "
                       "Вчерашний выпуск в пределах срока принят: удержание даёт хабу задержку не больше двух ночей, и она заметна.",
            "ok": not ok_old and not in_features_later and check["missing"] == 1}


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
    log, mem, filled = _journal_copy(sim)
    witness = HeadWitness()
    witness.heads = [] if filled else list(sim.witness[a].heads)
    if not witness.heads:  # ночей ещё не было: банк засвидетельствовал текущую голову
        seq0, h0 = log.head()
        witness.record({"seq": seq0, "hash": h0, "signature": sign_head(sim.net.hub.signing_key, seq0, h0),
                        "public_key": sim.net.hub.public_key})
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


def request_attribution(sim) -> dict:
    a, b = _pick_pair(sim)
    gw_a, gw_b = sim.net.gateways[a], _sandbox(sim.net.gateways[b])
    log, _, _ = _journal_copy(sim)
    base = log.head()[0]
    from datetime import timedelta
    now = sim.clock + timedelta(seconds=1)  # новый запрос: позже уже записанных (тот же запрос в ту же секунду: повтор)
    t1, t2 = _shared_transfers(sim, a, b)
    ref, other = gw_b.ref_hmac(t1), gw_b.ref_hmac(t2)
    # настоящий запрос: подписан шлюзом A, хаб пишет подпись в журнал и выдаёт квитанцию о включении
    sig = gw_a.sign_query(b, ref, now)
    resp = gw_b.query(a, ref, now, sig)
    genuine, h = log.append_seq(now, "query", a, b, ref_digest(ref), resp.get("error", "ok") + SIG_TAG + sig)
    receipt_ok = verify_head(sim.net.hub.public_key, genuine, h, sign_head(sim.net.hub.signing_key, genuine, h))
    # хаб от имени A: запрос без подписи (ответчик его не обслужит), подпись, снятая с настоящего запроса, и дубль
    refused = gw_b.query(a, other, now)
    forged = {"без подписи": log.append_seq(now, "query", a, b, ref_digest(other), refused.get("error", "ok"))[0],
              "с подписью от другого запроса": log.append_seq(now, "query", a, b, ref_digest(other), "ok" + SIG_TAG + sig)[0],
              "повтор настоящего запроса": log.append_seq(now, "query", a, b, ref_digest(ref), "ok" + SIG_TAG + sig)[0]}
    bad = set(unconfirmed_requests(log, a, gw_a.signer.public_key))
    earlier_bad = sorted(s for s in bad if s <= base)
    return {"title": "Хаб приписал банку запрос",
            "attack": f"Хаб пишет в журнал запросы «от банка {a}» к банку {b}, которых {a} не делал: так можно оговорить банк перед "
                      "регулятором или скрыть, кто на самом деле спрашивал. Журнал при этом цел: хэш-цепочка защищает только то, что хаб сам записал.",
            "rows": [["настоящий запрос", f"запись № {genuine}: подпись {a} верна" if genuine not in bad else "НЕ ПОДТВЕРЖДЁН"],
                     [f"квитанция {a} о включении", f"запись № {genuine}, голова подписана хабом" if receipt_ok else "подпись неверна"],
                     *[[f"запись хаба: {name}", f"№ {seq}: " + ("НЕ ПОДТВЕРЖДЕНА подписью банка" if seq in bad else "ПРИНЯТА")]
                       for name, seq in forged.items()],
                     [f"банк {b} на запрос без подписи", "отказ: подписи нет" if refused.get("error") == "unsigned" else "ОТВЕТИЛ"],
                     ["прежние записи журнала от этого банка", "все подписаны" if not earlier_bad else f"без подписи: {len(earlier_bad)}"]],
            "defense": "Шлюз банка подписывает каждый запрос (Ed25519: кто, кому, хэш ссылки, время), хаб пишет подпись в журнал "
                       "и отдаёт банку квитанцию: номер записи и подписанную голову. Ответчик не обслуживает запрос без подписи, "
                       "а любая запись без верной подписи или с повтором уже использованной видна при сверке журнала банком.",
            "ok": genuine not in bad and receipt_ok and set(forged.values()) <= bad and refused.get("error") == "unsigned"
                  and not earlier_bad}


def split_view(sim) -> dict:
    a, b = _pick_pair(sim)
    log_a, _, _ = _journal_copy(sim)
    log_b, _ = _clone_log(log_a)
    key, pk = sim.net.hub.signing_key, sim.net.hub.public_key
    # у банков уже есть головы прошлых ночей; дальше хаб ведёт два журнала: банку A один, банку B другой
    wa, wb = HeadWitness(), HeadWitness()
    for w, bank in ((wa, a), (wb, b)):
        w.heads, w.receipts = list(sim.witness[bank].heads), list(sim.witness[bank].receipts)
    now = sim.clock
    log_a.append(now, "query", a, b, ref_digest(secrets.token_hex(32)), "ok")
    log_b.append(now, "query", b, a, ref_digest(secrets.token_hex(32)), "not_found")  # другая история на тот же номер
    heads = {}
    for w, log, bank in ((wa, log_a, a), (wb, log_b, b)):
        seq, h = log.head()
        heads[bank] = (seq, h, w.record({"seq": seq, "hash": h, "signature": sign_head(key, seq, h), "public_key": pk}))
    own_a, own_b = wa.check(log_a), wb.check(log_b)
    split = wa.compare(wb)
    seq = heads[a][0]
    return {"title": "Хаб показал банкам разные журналы",
            "attack": f"Хаб ведёт две версии журнала: банку {a} показывает одну, банку {b} другую, и каждую подписывает своим настоящим "
                      "ключом. Внутри каждой версии цепочка цела, поэтому каждый банк по отдельности ничего не замечает.",
            "rows": [[f"голова у банка {a}", f"№ {seq}, {heads[a][1][:12]}…, подпись " + ("верна" if heads[a][2] else "неверна")],
                     [f"голова у банка {b}", f"№ {heads[b][0]}, {heads[b][1][:12]}…, подпись " + ("верна" if heads[b][2] else "неверна")],
                     ["проверка каждым банком своего журнала", "расхождений нет" if not own_a and not own_b else "РАСХОЖДЕНИЕ"],
                     [f"сверка голов {a} и {b}", f"РАСЩЕПЛЕНИЕ на записи № {', '.join(map(str, split))}" if split else "совпадают"]],
            "defense": "Банки обмениваются сохранёнными подписанными головами напрямую, не через хаб (в пилоте: по графику, "
                       "плюс регулятор как третий свидетель). Две головы с подписью хаба на один номер записи: доказательство расщепления журнала.",
            "ok": heads[a][2] and heads[b][2] and not own_a and not own_b and split == [seq]}

def answer_suppress(sim) -> dict:
    a, b = _pick_pair(sim)
    gw_b = _sandbox(sim.net.gateways[b])
    t1, t2 = _shared_transfers(sim, a, b)
    r1, r2 = gw_b.ref_hmac(t1), gw_b.ref_hmac(t2)
    day = sim.clock.date()
    gw_a = sim.net.gateways[a]

    def verdict(resp, ref):
        try:
            out = gw_a.open_answer(b, resp, ref, day)
        except ContextError:
            return "отклонён"
        return "ПРИНЯТ: " + out.get("error", "ответ")

    _ask(sim, a, gw_b, r1)  # настоящий ответ на первый запрос хаб выбрасывает
    forged = verdict({"error": "not_found"}, r1)  # и пишет отказ от имени банка
    stale = _ask(sim, a, gw_b, secrets.token_hex(32))  # настоящий запечатанный отказ на другой запрос
    reused = verdict(stale, r1)
    honest = verdict(_ask(sim, a, gw_b, r2), r2)
    return {"title": "Хаб подавил ответ",
            "attack": f"Банк {a} спросил банк {b} о переводе. Хаб выбрасывает ответ и отдаёт отказ «не найдено»: "
                      "сначала написанный сам, затем настоящий отказ банка на другой запрос.",
            "rows": [["отказ, написанный хабом", forged], ["чужой настоящий отказ", reused], ["честный ответ", honest]],
            "defense": "Отказ тоже запечатан ключом банка-ответчика вместе со ссылкой и днём. Хаб видит статус для журнала, "
                       "но не может ни написать отказ сам, ни подсунуть отказ на другой запрос.",
            "ok": forged == "отклонён" and reused == "отклонён" and honest.startswith("ПРИНЯТ")}


def _cover_run(sim, quiet: bool) -> dict:
    """Злонамеренный банк прикрывает своих дропов в ночных выпусках: грубо (балл 0) или незаметно
    (медианный балл выпуска). Ловить должна ретроспективная сверка выпусков с инцидентами."""
    from datetime import timedelta
    from statistics import median

    from protocol.reputation import ReputationBook

    incidents = []  # (банк, счёт, день): счёт участвовал в подтверждённом отмывочном переводе
    seen = set()
    for _, t, fb, fa, tb, ta, *_rest, laundering in sim.timeline:
        if not laundering:
            continue
        for b, acc in ((fb, fa), (tb, ta)):
            if b in sim.participants and (b, acc) not in seen:
                seen.add((b, acc))
                incidents.append((b, acc, t.date()))
    counts = Counter(b for b, _, _ in incidents)
    attacker = counts.most_common(1)[0][0]

    own_drops = {acc for b, acc, _ in incidents if b == attacker}  # банк знает своих дропов и прикрывает их

    def release(bank, day):
        rel = sim.node_p.get((bank, day - timedelta(days=1)))
        if rel is None or bank != attacker:
            return rel
        cover = median(rel.values()) if quiet else 0.0
        return {a: (cover if a in own_drops else s) for a, s in rel.items()}

    book, honest_book, flagged_at = ReputationBook(), ReputationBook(), None
    for b, acc, day in incidents:
        rel = release(b, day)
        if rel is None or acc not in rel:
            continue
        book.add_incident(b, acc, rel)
        honest_book.add_incident(b, acc, sim.node_p[(b, day - timedelta(days=1))])
        if b == attacker and flagged_at is None and book.verdict(b).status == "на проверку":
            flagged_at = book.verdict(b).n
    verdicts = book.verdicts()
    v = verdicts[attacker]
    judged = [x for x in verdicts.values() if x.bank != attacker and x.status != "мало данных"]
    honest_flagged = [x.bank for x in judged if x.status == "на проверку"]
    caught = v.status == "на проверку"
    honest_q = honest_book.verdict(attacker).quality
    rows = [[f"качество выпусков банка {attacker}", f"{v.quality:.2f} (случайный выпуск: 0,50)".replace(".", ",", 1)],
            ["то же, если бы банк был честен", f"{honest_q:.2f}".replace(".", ",")],
            ["подтверждённых инцидентов банка", v.n],
            ["граница интервала сверху", f"{v.hi:.2f}".replace(".", ",")],
            ["решение", f"{v.status.upper()}: {v.reason}" if caught else f"{v.status}: {v.reason}"],
            ["флаг поставлен на инциденте №", flagged_at if flagged_at else "не поставлен"],
            ["честных банков с выборкой", f"{len(judged)}, из них на проверку: {len(honest_flagged)}"]]
    if not quiet:
        return {"title": "Банк покрывает дропов",
                "attack": f"Банк {attacker} в сговоре со своими дропами: в ночных выпусках ставит им балл 0, "
                          "чтобы соседи пропустили переводы к ним. Антифрод-центр сверяет выпуски задним числом "
                          "с подтверждёнными инцидентами: какой балл банк дал счёту накануне.",
                "rows": rows,
                "defense": "Репутация банка: процентиль балла каждого подтверждённого дропа в выпуске его банка за ночь до инцидента. "
                           "Если оценки хуже случайных (граница интервала Уилсона ниже 0,5) или резко хуже соседей, "
                           "выпуски банка перестают подаваться в модели соседей до разбора регулятором. "
                           "Ловится только грубое прикрытие: незаметный вариант разобран в атаке «Банк прикрывает дропов незаметно».",
                "ok": caught}
    return {"title": "Банк прикрывает дропов незаметно",
            "attack": f"Банк {attacker} ставит своим известным дропам не 0, а медианный балл своего же выпуска: дроп выглядит "
                      "обычным клиентом, не лучше и не хуже прочих. Соседи всё равно не получают сигнала о дропе.",
            "rows": rows,
            "defense": ("Защита не сработала, и это ожидаемо: сверка ловит выпуски хуже случайных, а медиана даёт дропу процентиль "
                        "около 0,5, то есть ровно «случайный» выпуск. Против соседей (медиана минус 0,15) банк тоже не выделяется: "
                        "на малой выборке интервал широкий, а честные выпуски сами бывают около 0,5. Вместо этого: выборочный "
                        "аудит регулятором (сверка выпуска с моделью и данными банка по выбранным дропам), а вред ограничен тем, "
                        "что такой банк просто не помогает соседям, как неучастник. Польза самой оценки соседа на HI-Small "
                        "не доказана (experiments/results/ai_review.md, раздел B)." if not caught else
                        "Сверка выпусков с инцидентами поймала и незаметное прикрытие на этой выборке."),
            "ok": caught}


def bank_covers_drops(sim) -> dict:
    return _cover_run(sim, quiet=False)


def bank_covers_drops_quietly(sim) -> dict:
    return _cover_run(sim, quiet=True)


ATTACKS = {"brute": brute_force, "foreign": foreign_transfer, "quota": quota_drain,
           "journal": journal_tamper, "signature": signature_forgery, "eavesdrop": hub_eavesdrop,
           "swap": answer_swap, "replay": release_replay, "withhold": release_withhold,
           "redirect": release_redirect, "length": length_leak,
           "rewrite": history_rewrite, "attribute": request_attribution, "split": split_view,
           "suppress": answer_suppress, "cover": bank_covers_drops, "cover_quiet": bank_covers_drops_quietly}

# Санитарные проверки: базовые свойства криптографии и журнала (перебор, подпись головы,
# правка журнала без пересчёта, чтение шифротекста). Остальное: атаки на устройство протокола.
SANITY_CHECKS = frozenset({"brute", "signature", "journal", "eavesdrop"})

# Атаки, которые ожидаемо НЕ закрыты: стенд честно показывает «защита не сработала» и что вместо неё.
OPEN_ATTACKS = frozenset({"cover_quiet"})


def run(sim, name: str) -> dict:
    with sim.lock:
        out = ATTACKS[name](sim)
    return {"id": name, **out, "group": "sanity" if name in SANITY_CHECKS else "protocol", "open": name in OPEN_ATTACKS}
