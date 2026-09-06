#!/usr/bin/env python3
"""
금 프리미엄 알림 — 두 ETF의 NAV 비율로 프리미엄 수준을 복원한다.

  TIGER KRX금현물 (0072R0)  : 국내 금값 + 프리미엄
  KODEX 금액티브   (0064K0)  : 국제 금값

  프리미엄(%) ≈ (오늘비율 / 앵커비율 - 1) × 100 + 앵커프리미엄
  오늘비율 = TIGER_NAV / KODEX_NAV

KRX 스크래핑도 증권사 API도 쓰지 않는다. ETF NAV만 있으면 된다.

사용법:
  python etf_premium.py                # 매일 알림
  python etf_premium.py --probe        # 응답 원문 확인
  python etf_premium.py --reanchor -0.03   # goldkimp 값으로 앵커 재설정
"""

import os
import sys
import json
import argparse
import datetime as dt

import requests

TIGER, KODEX = "0072R0", "0064K0"
WATCH, ACT, BACK = 3.0, 5.0, 1.0
ANCHOR_PATH = "anchor.json"
LOG_PATH = "premium_log.csv"

NAVER_ETF = "https://finance.naver.com/api/sise/etfItemList.nhn"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36")


def tg(text):
    tok, chat = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if not tok or not chat:
        print("[경고] 텔레그램 미설정 — 콘솔 출력만 합니다.\n" + text)
        return
    r = requests.post(f"https://api.telegram.org/bot{tok}/sendMessage",
                      json={"chat_id": chat, "text": text}, timeout=20)
    print("텔레그램:", "전송 완료" if r.ok else f"실패 {r.status_code} {r.text[:200]}")


def fetch_navs(probe=False):
    """네이버 금융에서 전체 ETF 목록을 한 번에 받아 두 종목만 골라낸다."""
    r = requests.get(NAVER_ETF, headers={"User-Agent": UA,
                                         "Referer": "https://finance.naver.com/sise/etf.naver"},
                     timeout=25)
    r.raise_for_status()
    items = r.json().get("result", {}).get("etfItemList", [])
    if not items:
        raise ValueError("ETF 목록이 비어 있습니다 (응답 구조 변경 가능)")

    found = {}
    for it in items:
        code = str(it.get("itemcode", "")).strip()
        if code in (TIGER, KODEX):
            found[code] = it
            if probe:
                print(f"  {code}: {json.dumps(it, ensure_ascii=False)}")

    missing = {TIGER, KODEX} - set(found)
    if missing:
        raise ValueError(f"종목을 찾지 못했습니다: {missing} (전체 {len(items)}개 중)")

    out = {}
    for code, it in found.items():
        nav = float(it.get("nav") or 0)
        close = float(it.get("nowVal") or 0)
        if nav <= 0:
            print(f"  [경고] {code} NAV 없음 -> 종가 {close} 사용 (오차 커짐)",
                  file=sys.stderr)
            nav = close
        out[code] = {"nav": nav, "close": close, "name": it.get("itemname", code)}
    return out


def load_anchor():
    if not os.path.exists(ANCHOR_PATH):
        sys.exit(f"[중지] {ANCHOR_PATH} 가 없습니다.")
    a = json.load(open(ANCHOR_PATH, encoding="utf-8"))
    for k in ("ratio", "premium", "date"):
        if k not in a:
            sys.exit(f"[중지] {ANCHOR_PATH} 에 '{k}' 가 없습니다.")
    return a


def save_anchor(a):
    json.dump(a, open(ANCHOR_PATH, "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)
    print(f"{ANCHOR_PATH} 갱신:\n{json.dumps(a, ensure_ascii=False, indent=2)}")


def append_log(date, ratio, prem, t, k):
    new = not os.path.exists(LOG_PATH)
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        if new:
            f.write("date,tiger_nav,kodex_nav,ratio,premium_calc,premium_actual\n")
        f.write(f"{date},{t:.2f},{k:.2f},{ratio:.6f},{prem:.3f},\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--probe", action="store_true", help="응답 원문 출력")
    ap.add_argument("--reanchor", type=float, metavar="PREMIUM",
                    help="goldkimp 등에서 확인한 실제 프리미엄(%%)으로 앵커 재설정")
    a = ap.parse_args()

    try:
        nav = fetch_navs(a.probe)
    except Exception as e:                           # noqa: BLE001
        tg(f"⚠️ 금 프리미엄 봇 오류\n\n{type(e).__name__}\n{str(e)[:400]}")
        raise

    t, k = nav[TIGER]["nav"], nav[KODEX]["nav"]
    ratio = t / k
    today = dt.date.today().isoformat()

    # ---- 재앵커링 모드 ----
    if a.reanchor is not None:
        old = load_anchor() if os.path.exists(ANCHOR_PATH) else None
        new = {"date": today, "ratio": round(ratio, 6), "premium": a.reanchor,
               "source": "수동 재앵커링", "tiger_nav": t, "kodex_nav": k}
        save_anchor(new)
        msg = (f"🔧 앵커 재설정 완료\n\n"
               f"기준일 {today}\n"
               f"TIGER NAV {t:,.2f}\nKODEX NAV {k:,.2f}\n"
               f"비율 {ratio:.6f}\n실제 프리미엄 {a.reanchor:+.2f}%\n")
        if old:
            drift = (ratio / old["ratio"] - 1) * 100 + old["premium"] - a.reanchor
            days = (dt.date.fromisoformat(today)
                    - dt.date.fromisoformat(old["date"])).days
            msg += (f"\n이전 앵커 {old['date']} ({days}일 경과)\n"
                    f"누적 드리프트 {drift:+.3f}%p")
        tg(msg)
        return

    # ---- 평소 모드 ----
    anc = load_anchor()
    prem = (ratio / anc["ratio"] - 1) * 100 + anc["premium"]
    days = (dt.date.fromisoformat(today) - dt.date.fromisoformat(anc["date"])).days

    if prem >= ACT:
        head = "🚨 전환 신호"
        note = "KODEX 금액티브(0064K0)로 교체.\n오늘 09:05~15:20 사이, 지정가로."
    elif prem >= WATCH:
        head = "⚠️ 관찰"
        note = f"{ACT:.0f}% 돌파는 보통 1~2일 안에 옵니다."
    elif prem <= BACK:
        head = "✅ 평상"
        note = "국제금 보유 중이면 TIGER(0072R0)로 복귀."
    else:
        head = "· 중립"
        note = "그대로 유지."

    warn = "\n\n⚠️ 앵커가 오래됐습니다. 재앵커링 권장." if days >= 35 else ""

    tg(f"{head}  |  금 프리미엄 {prem:+.2f}%\n\n"
       f"TIGER NAV  {t:,.2f}\n"
       f"KODEX NAV  {k:,.2f}\n"
       f"비율 {ratio:.6f}\n\n"
       f"{note}\n\n"
       f"기준: {ACT:.0f}% 전환 · {BACK:.0f}% 복귀\n"
       f"앵커 {anc['date']} ({days}일 경과){warn}")

    append_log(today, ratio, prem, t, k)
    print(f"프리미엄 {prem:+.2f}%  비율 {ratio:.6f}  앵커 {days}일 경과")


if __name__ == "__main__":
    main()
