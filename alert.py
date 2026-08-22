#!/usr/bin/env python3
"""
KRX 금 프리미엄을 매일 계산해 텔레그램으로 보낸다.

필요한 환경변수 (GitHub Secrets):
  TELEGRAM_BOT_TOKEN   BotFather가 준 토큰
  TELEGRAM_CHAT_ID     내 채팅 ID
  KRX_BLD_DOM          국내 금현물 조회용 bld 값
  KRX_BLD_INTL         국제금시세 조회용 bld 값
  KRX_ISU_CD           금 99.99_1Kg 종목코드
"""

import os
import sys
import time
import datetime as dt

import requests

WATCH, ACT, BACK = 3.0, 5.0, 1.0          # 관찰 / 전환 / 복귀 임계값
KRX_URL = "https://data.krx.co.kr/comm/bldAttendant/getJsonData.cmd"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36")


def tg(text):
    tok, chat = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if not tok or not chat:
        sys.exit("[중지] TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID 가 설정되지 않았습니다.")
    r = requests.post(f"https://api.telegram.org/bot{tok}/sendMessage",
                      json={"chat_id": chat, "text": text}, timeout=20)
    if not r.ok:
        sys.exit(f"[중지] 텔레그램 전송 실패 {r.status_code}: {r.text[:200]}")
    print("텔레그램 전송 완료")


def krx(bld, menu, extra=None):
    """KRX 일별 시세 조회. 최근 10일치를 받아 가장 최신 행을 쓴다."""
    end = dt.date.today()
    start = end - dt.timedelta(days=14)
    payload = {"bld": bld, "strtDd": start.strftime("%Y%m%d"),
               "endDd": end.strftime("%Y%m%d"), "share": "1", "money": "1",
               "csvxls_isNo": "false", **(extra or {})}
    headers = {"User-Agent": UA, "X-Requested-With": "XMLHttpRequest",
               "Referer": "https://data.krx.co.kr/contents/MDC/MDI/mdiLoader/"
                          f"index.cmd?menuId={menu}"}
    last = None
    for k in range(3):
        try:
            r = requests.post(KRX_URL, data=payload, headers=headers, timeout=30)
            r.raise_for_status()
            rows = r.json().get("output") or r.json().get("OutBlock_1") or []
            if not rows:
                raise ValueError("응답이 비어 있음")
            return rows
        except Exception as e:                       # noqa: BLE001
            last = e
            print(f"  재시도 {k+1}/3: {e}", file=sys.stderr)
            time.sleep(3 * (k + 1))
    raise RuntimeError(f"KRX 조회 실패: {last}")


def num(s):
    return float(str(s).replace(",", "").strip())


def pick(rows, *cands):
    """행 목록에서 날짜가 가장 최근인 행의 가격 컬럼을 찾아 반환"""
    row = max(rows, key=lambda r: r.get("TRD_DD", ""))
    for c in cands:
        if c in row and str(row[c]).strip() not in ("", "-"):
            return row.get("TRD_DD", "?"), num(row[c])
    raise KeyError(f"가격 컬럼 없음. 사용 가능: {list(row.keys())}")


def main():
    try:
        dom = krx(os.environ["KRX_BLD_DOM"], "MDC0201060201",
                  {"isuCd": os.environ.get("KRX_ISU_CD", "")})
        time.sleep(1)
        itl = krx(os.environ["KRX_BLD_INTL"], "MDC0201060207")
        d1, k = pick(dom, "TDD_CLSPRC", "CLSPRC")
        d2, i = pick(itl, "KRW_G_CLSPRC", "TDD_CLSPRC", "CLSPRC")
    except Exception as e:                           # noqa: BLE001
        tg(f"⚠️ 금 프리미엄 봇 오류\n\n{type(e).__name__}: {str(e)[:300]}")
        raise

    prem = (k / i - 1) * 100

    if prem >= ACT:
        head = "🚨 전환 신호"
        note = "KODEX 금액티브(0064K0)로 교체하세요.\n다음 거래일 장중, 지정가로."
    elif prem >= WATCH:
        head = "⚠️ 관찰"
        note = f"{ACT:.0f}% 돌파는 보통 1~2일 안에 옵니다.\n계좌 로그인 확인해두세요."
    elif prem <= BACK:
        head = "✅ 평상"
        note = ("국제금 보유 중이라면 ACE KRX금현물(411060)로 복귀.\n"
                "국내금 보유 중이라면 그대로.")
    else:
        head = "· 중립"
        note = "그대로 유지."

    tg(f"{head}  |  금 프리미엄 {prem:+.2f}%\n"
       f"기준일 {d1}\n\n"
       f"KRX 금현물   {k:,.0f} 원/g\n"
       f"국제금 환산  {i:,.0f} 원/g\n\n"
       f"{note}\n\n"
       f"기준: {ACT:.0f}% 이상 전환 · {BACK:.0f}% 이하 복귀")
    print(f"프리미엄 {prem:+.2f}%")


if __name__ == "__main__":
    main()
