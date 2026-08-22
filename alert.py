#!/usr/bin/env python3
"""
KRX 금 프리미엄을 매일 계산해 텔레그램으로 보낸다.

[설정 방식이 바뀌었습니다]
파라미터를 하나씩 넣는 대신, 개발자도구에서 본 요청 본문을 통째로 붙여넣는다.
그래야 KRX 화면마다 다른 파라미터 이름을 추측하지 않아도 된다.

필요한 GitHub Secrets:
  TELEGRAM_BOT_TOKEN    BotFather 토큰
  TELEGRAM_CHAT_ID  내 채팅 ID
  KRX_PAYLOAD_DOM   개별종목 시세 추이 화면의 요청 본문 전체
  KRX_PAYLOAD_INTL  국제금시세 동향 화면의 요청 본문 전체
  KRX_URL_DOM       개별종목 시세 추이 화면의 주소창 URL (menuId 포함)
  KRX_URL_INTL      국제금시세 동향 화면의 주소창 URL (menuId 포함)

요청 본문 복사법:
  F12 → Network → Fetch/XHR → 조회 클릭 → getJsonData.cmd 클릭
  → Payload 탭 → 오른쪽 위 "view source" 클릭
  → bld=...&locale=ko_KR&... 형태의 한 줄이 나오면 전체 복사
"""

import os
import re
import sys
import time
import datetime as dt
from urllib.parse import parse_qsl, unquote_plus

import requests

WATCH, ACT, BACK = 3.0, 5.0, 1.0
KRX_URL = "https://data.krx.co.kr/comm/bldAttendant/getJsonData.cmd"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36")

# 가격으로 쓸 만한 컬럼 후보 (앞쪽 우선)
PRICE_KEYS = ["KRW_G_CLSPRC", "TDD_CLSPRC", "CLSPRC", "PRC", "ISU_PRC"]
DATE_KEYS = ["TRD_DD", "BAS_DD", "TRD_YMD"]


def tg(text):
    tok, chat = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if not tok or not chat:
        sys.exit("[중지] TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID 미설정")
    r = requests.post(f"https://api.telegram.org/bot{tok}/sendMessage",
                      json={"chat_id": chat, "text": text}, timeout=20)
    print("텔레그램:", "전송 완료" if r.ok else f"실패 {r.status_code} {r.text[:200]}")


def build_payload(raw):
    """붙여넣은 요청 본문을 dict 로 바꾸고 날짜만 최신으로 교체"""
    raw = raw.strip()
    if raw.startswith("{"):
        sys.exit("[중지] JSON 형식입니다. Payload 탭에서 'view source'를 눌러 "
                 "bld=...&... 형태로 복사하세요.")
    d = dict(parse_qsl(raw, keep_blank_values=True))
    if "bld" not in d:
        sys.exit(f"[중지] bld 가 없습니다. 복사한 내용: {raw[:200]}")

    today = dt.date.today()
    start = today - dt.timedelta(days=21)
    for k in list(d):
        if k in ("strtDd", "strtDd1", "startDd"):
            d[k] = start.strftime("%Y%m%d")
        elif k in ("endDd", "endDd1", "trdDd", "basDd"):
            d[k] = today.strftime("%Y%m%d")
    return d


DEFAULT_PAGE = "https://data.krx.co.kr/contents/MDC/MDI/mdiLoader/index.cmd"


def make_session():
    """KRX는 세션 쿠키만으로는 부족하고, 해당 화면을 실제로 열어본 세션이어야
       그 화면의 bld 요청을 받아준다. 그래서 메인 -> 조회화면 순으로 방문한다."""
    s = requests.Session()
    s.headers.update({
        "User-Agent": UA,
        "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
    })
    try:
        r = s.get("https://data.krx.co.kr/", timeout=20)
        print(f"  세션 준비 메인 -> HTTP {r.status_code}", file=sys.stderr)
    except Exception as e:                           # noqa: BLE001
        print(f"  세션 준비 실패: {e}", file=sys.stderr)
    time.sleep(0.5)
    return s


def open_page(sess, url, label):
    """조회 화면을 실제로 방문해 해당 bld 를 세션에 등록시킨다."""
    url = (url or DEFAULT_PAGE).strip()
    try:
        r = sess.get(url, timeout=20)
        print(f"  [{label}] 화면 방문 -> HTTP {r.status_code}  "
              f"쿠키 {list(sess.cookies.keys())}", file=sys.stderr)
    except Exception as e:                           # noqa: BLE001
        print(f"  [{label}] 화면 방문 실패: {e}", file=sys.stderr)
    time.sleep(1.0)
    return url


def krx(sess, raw, label, page_url=None):
    payload = build_payload(raw)
    referer = open_page(sess, page_url, label)
    headers = {
        "X-Requested-With": "XMLHttpRequest",
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "Referer": referer,
        "Origin": "https://data.krx.co.kr",
    }
    last = None
    for k in range(3):
        try:
            r = sess.post(KRX_URL, data=payload, headers=headers, timeout=30)
            body = r.text.strip()
            if r.status_code != 200 or body.upper().startswith("LOGOUT"):
                print(f"  [{label}] HTTP {r.status_code}\n"
                      f"  보낸 키: {sorted(payload)}\n"
                      f"  쿠키: {list(sess.cookies.keys())}\n"
                      f"  응답: {body[:400]}", file=sys.stderr)
                if body.upper().startswith("LOGOUT"):
                    raise RuntimeError("세션 만료(LOGOUT). 쿠키 재발급 후 재시도")
                r.raise_for_status()
            js = r.json()
            rows = js.get("output") or js.get("OutBlock_1") or js.get("block1") or []
            if not rows:
                print(f"  [{label}] 응답 구조: {list(js)[:10]}", file=sys.stderr)
                raise ValueError("데이터 행이 비어 있음 (조회 기간/종목 확인)")
            return rows
        except Exception as e:                       # noqa: BLE001
            last = e
            print(f"  [{label}] 재시도 {k+1}/3: {e}", file=sys.stderr)
            time.sleep(3 * (k + 1))
            if "LOGOUT" in str(e):
                sess.cookies.clear()
                try:
                    sess.get("https://data.krx.co.kr/", timeout=20)
                except Exception:                    # noqa: BLE001, S110
                    pass
                open_page(sess, page_url, label)
    raise RuntimeError(f"{label} 조회 실패: {last}")


def latest(rows, label):
    dk = next((k for k in DATE_KEYS if k in rows[0]), None)
    row = max(rows, key=lambda r: r.get(dk, "")) if dk else rows[0]
    for pk in PRICE_KEYS:
        v = str(row.get(pk, "")).replace(",", "").strip()
        if re.fullmatch(r"\d+(\.\d+)?", v):
            return row.get(dk, "?"), float(v), pk
    sys.exit(f"[중지] {label}: 가격 컬럼을 찾지 못했습니다.\n"
             f"       사용 가능한 컬럼: {list(row)}\n"
             f"       샘플 행: {row}")


def main():
    try:
        sess = make_session()
        dom = krx(sess, os.environ["KRX_PAYLOAD_DOM"], "국내",
                  os.environ.get("KRX_URL_DOM"))
        time.sleep(1)
        itl = krx(sess, os.environ["KRX_PAYLOAD_INTL"], "국제",
                  os.environ.get("KRX_URL_INTL"))
        d1, k, ck = latest(dom, "국내")
        d2, i, ci = latest(itl, "국제")
        print(f"국내 {d1} {k:,.0f} ({ck}) / 국제 {d2} {i:,.0f} ({ci})")
    except Exception as e:                           # noqa: BLE001
        tg(f"⚠️ 금 프리미엄 봇 오류\n\n{type(e).__name__}\n{str(e)[:400]}\n\n"
           f"Actions 로그에서 상세 내용을 확인하세요.")
        raise

    prem = (k / i - 1) * 100
    if prem >= ACT:
        head, note = "🚨 전환 신호", "KODEX 금액티브(0064K0)로 교체.\n오늘 장중, 지정가로."
    elif prem >= WATCH:
        head, note = "⚠️ 관찰", f"{ACT:.0f}% 돌파는 보통 1~2일 안에 옵니다."
    elif prem <= BACK:
        head, note = "✅ 평상", "국제금 보유 중이면 ACE(411060)로 복귀."
    else:
        head, note = "· 중립", "그대로 유지."

    tg(f"{head}  |  금 프리미엄 {prem:+.2f}%\n"
       f"기준일 {d1}\n\n"
       f"KRX 금현물   {k:,.0f} 원/g\n"
       f"국제금 환산  {i:,.0f} 원/g\n\n"
       f"{note}\n\n"
       f"기준: {ACT:.0f}% 이상 전환 · {BACK:.0f}% 이하 복귀")


if __name__ == "__main__":
    main()
