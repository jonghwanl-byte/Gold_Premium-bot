import requests
import time
import datetime
import os
import json
import matplotlib.pyplot as plt
from io import BytesIO
import yfinance as yf
import openai
import traceback

# ---------- 환경 변수 및 초기 설정 ----------
BOT_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_TO")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

if not BOT_TOKEN or not CHAT_ID:
    raise EnvironmentError("FATAL ERROR: TELEGRAM_TOKEN or TELEGRAM_TO is not set in environment.")

try:
    openai_client = openai.OpenAI(api_key=OPENAI_API_KEY)
except Exception:
    openai_client = None

DATA_FILE = "gold_premium_history.json"
TROY_Ounce_TO_GRAM = 31.1035 

# ---------- 헬퍼 함수 ----------
def timestamp_to_kst(timestamp):
    if timestamp is None:
        return "N/A"
    dt_object = datetime.datetime.fromtimestamp(timestamp, datetime.timezone.utc)
    kst_tz = datetime.timezone(datetime.timedelta(hours=9))
    kst_dt = dt_object.astimezone(kst_tz)
    return kst_dt.strftime('%Y-%m-%d %H:%M:%S KST')

def send_telegram_text(msg):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": msg}
    try:
        r = requests.post(url, json=payload, timeout=10)
        r.raise_for_status()
    except requests.exceptions.RequestException as e:
        raise RuntimeError(f"텔레그램 메시지 발송 실패: {e}")

def send_telegram_photo(image_bytes, caption=""):
    files = {"photo": image_bytes}
    data = {"chat_id": CHAT_ID, "caption": caption}
    response = requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto", files=files, data=data, timeout=10)
    response.raise_for_status()

# 1. 국내 금 ETF 데이터 (현재가, 전일종가, NAV)
def get_korean_gold_data():
    symbol = "411060.KS"  
    try:
        ticker = yf.Ticker(symbol)
        data = ticker.info
        
        market_price = data.get('regularMarketPrice')
        prev_close = data.get('previousClose') # 전일 종가
        nav_price = data.get('navPrice')
        market_time = data.get('regularMarketTime')
        
        if market_price is None:
            market_price = prev_close
            
        if market_price is None:
             raise ValueError(f"Yahoo Finance: '{symbol}'의 유효한 시장 가격을 찾을 수 없습니다.")
             
        return market_price, prev_close, nav_price, market_time
    except Exception as e:
        raise RuntimeError(f"KRX 골드 ETF 조회 실패: {type(e).__name__} - {e}")

# 2. Yahoo Finance 가격 조회 (현재가, 전일종가 반환)
def get_yahoo_price_pair(symbol):
    try:
        ticker = yf.Ticker(symbol)
        data = ticker.info
        price = data.get('regularMarketPrice')
        prev = data.get('previousClose')
        
        if price is None: price = prev
        if price is None:
            raise ValueError(f"Yahoo Finance: '{symbol}' 데이터 누락.")
            
        return price, prev
    except Exception as e:
        raise RuntimeError(f"Yahoo Finance '{symbol}' 조회 실패: {type(e).__name__} - {e}")

# 3. 모든 데이터 가져오기
def get_gold_and_fx_data():
    usd_krw, usd_krw_prev = get_yahoo_price_pair("USDKRW=X")
    gold_usd, gold_usd_prev = get_yahoo_price_pair("GC=F")
    
    etf_price, etf_prev, etf_nav, etf_time = get_korean_gold_data()
    
    return {
        "etf_now": etf_price,
        "etf_prev": etf_prev,
        "etf_nav": etf_nav,
        "etf_time": etf_time,
        "usd_now": usd_krw,
        "usd_prev": usd_krw_prev,
        "gold_now": gold_usd,
        "gold_prev": gold_usd_prev
    }

# ---------- 데이터 처리 및 분석 ----------
def load_history():
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r") as f:
                return json.load(f)
        except json.JSONDecodeError:
            return []
    return []

def save_history(data):
    data = data[-100:]
    with open(DATA_FILE, "w") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

# (핵심) calc_premium: NAV 누락 시 '동적 비율'로 추정 NAV 계산
def calc_premium():
    d = get_gold_and_fx_data()
    
    market_price = d['etf_now']
    nav_price = d['etf_nav']
    warning_msg = ""
    
    # 1. NAV 데이터가 유효한 경우 (가장 정확)
    if nav_price is not None and nav_price > 0:
        theoretical_nav = nav_price
        # warning_msg = "" # 정상
        
    # 2. NAV 데이터 누락 시: 전일 종가 비율을 이용한 역산 (백업 로직)
    else:
        # 전일 기준 국제 금값(원화 환산, 1g 기준 아님, 순수 비율용)
        # 전일 국제 금값(KRW) = (전일 골드($) / 31.1035) * 전일 환율
        # 하지만 단위가 중요하지 않으므로, 비율(Multiplier)만 구합니다.
        
        if d['etf_prev'] and d['gold_prev'] and d['usd_prev']:
            # 전일 국제 1g 원화 가격
            yesterday_1g_krw = (d['gold_prev'] / TROY_Ounce_TO_GRAM) * d['usd_prev']
            
            # ETF가 1g 대비 몇 배의 가치를 가지는지 비율 계산 (단위 보정 계수)
            # 예: ETF가 27000원, 1g이 190000원이면 ratio는 약 0.14
            conversion_ratio = d['etf_prev'] / yesterday_1g_krw
            
            # 오늘 실시간 1g 원화 가격
            today_1g_krw = (d['gold_now'] / TROY_Ounce_TO_GRAM) * d['usd_now']
            
            # 보정 계수를 적용한 오늘의 '추정 NAV'
            theoretical_nav = today_1g_krw * conversion_ratio
            
            warning_msg = "⚠️ NAV 누락: 전일 종가 비율로 추정된 NAV 사용"
        else:
            # 전일 데이터조차 없으면 계산 불가
            theoretical_nav = market_price # 괴리율 0으로 만듦
            warning_msg = "⚠️ 데이터 부족으로 괴리율 계산 불가"

    # 프리미엄 계산
    premium = (market_price / theoretical_nav - 1) * 100
    
    return {
        "korean": market_price,
        "international_krw": theoretical_nav,
        "usd_krw": d['usd_now'],
        "gold_usd": d['gold_now'],
        "premium": premium,
        "market_time": d['etf_time'],
        "warning_msg": warning_msg
    }

def create_graph(history):
    history = history[-7:]
    if len(history) < 2: return None
    dates = [x["date"] for x in history]
    premiums = [x["premium"] for x in history]

    plt.figure(figsize=(6, 3))
    plt.plot(dates, premiums, marker="o")
    plt.title("ETF Premium Trend (%)")
    plt.ylabel("Premium (%)")
    plt.xticks(rotation=45, ha='right')
    plt.grid(True, alpha=0.3)
    plt.tight_layout()

    buf = BytesIO()
    plt.savefig(buf, format="png")
    plt.close()
    buf.seek(0)
    return buf

def analyze_with_ai(today_msg, history):
    if not openai_client:
        return "AI 분석 오류: OpenAI 클라이언트 초기화 실패"
    
    prompt = f"""
다음은 최근 7일간의 ACE KRX금현물 ETF 괴리율 데이터입니다.
{json.dumps(history[-7:], ensure_ascii=False, indent=2)}

오늘의 주요 데이터:
{today_msg}

이 데이터를 기반으로 괴리율(프리미엄) 상태와 투자 관점 요약을 3줄 이내로 설명해줘.
"""
    try:
        response = openai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.6,
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        return f"AI 분석 오류: {e}"

def main():
    try:
        today = datetime.date.today().isoformat()
        
        # 변수 초기화
        current_premium = None
        change = 0.0
        time_str = ""
        level = "N/A"
        trend = "N/A"
        avg7 = 0.0
        
        info = calc_premium()
        history = load_history()
        
        current_premium = info["premium"]
        
        # 집계 시간 처리
        if info["market_time"]:
            time_str = timestamp_to_kst(info["market_time"])
        else:
            time_str = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S KST')

        # 히스토리 저장
        new_history_data = {
            "date": today, 
            "premium": round(current_premium, 2),
            "time_kst": time_str
        }
        
        if history and history[-1]["date"] == today:
            history[-1] = new_history_data
        else:
            history.append(new_history_data)
        
        save_history(history)

        # 통계 계산
        prev_premium_data = [h for h in history if h["date"] != today]
        prev = prev_premium_data[-1]["premium"] if prev_premium_data else info["premium"]
        change = info["premium"] - prev
        
        last7 = [x["premium"] for x in history[-7:]]
        avg7 = sum(last7)/len(last7) if last7 else 0
        level = "고평가" if info["premium"] > avg7 else "저평가"
        trend = "📈 상승세" if change > 0 else "📉 하락세"
            
        # 메시지 구성
        msg_data = (
            f"📅 {today} ACE KRX금현물 ETF 괴리율 알림\n"
            f"기준 일시: {time_str}\n"
            f"{info['warning_msg']}\n"
            f"국내 ETF 시장가: {info['korean']:,.0f}원\n"
            f"추정/실제 NAV: {info['international_krw']:,.0f}원\n"
            f"국제 금시세: ${info['gold_usd']:,.2f}/oz\n"
            f"환율: {info['usd_krw']:,.2f}원/$\n"
            f"👉 괴리율: {info['premium']:+.2f}% ({change:+.2f}% vs 전일)\n"
            f"최근 7일 평균({avg7:.2f}%) 대비: {level} {trend}"
        )
        
        ai_summary = analyze_with_ai(msg_data, history)
        full_msg = f"{msg_data}\n\n🤖 AI 요약:\n{ai_summary}"

        send_telegram_text(full_msg)

        graph_buf = create_graph(history)
        if graph_buf:
            send_telegram_photo(graph_buf, caption="📈 괴리율 추세")

    except Exception as e:
        error_msg = f"🔥 오류 발생: {type(e).__name__} - {e}\n{traceback.format_exc()}"
        print(error_msg)
        try:
            send_telegram_text(error_msg[:4000])
        except:
            pass

if __name__ == "__main__":
    main()
