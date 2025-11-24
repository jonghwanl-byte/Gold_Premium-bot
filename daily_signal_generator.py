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
        # 디버그 로그는 생략하거나 필요시 주석 해제
        # print(f"Status Code: {r.status_code}") 
        r.raise_for_status()
    except requests.exceptions.RequestException as e:
        raise RuntimeError(f"텔레그램 메시지 발송 실패: {e}")

def send_telegram_photo(image_bytes, caption=""):
    files = {"photo": image_bytes}
    data = {"chat_id": CHAT_ID, "caption": caption}
    response = requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto", files=files, data=data, timeout=10)
    response.raise_for_status()

# 1. 국내 금 가격 대용: ACE KRX금현물 ETF
def get_korean_gold_data():
    symbol = "411060.KS"  
    try:
        ticker = yf.Ticker(symbol)
        data = ticker.info
        
        market_price = data.get('regularMarketPrice')
        nav_price = data.get('navPrice')
        market_time = data.get('regularMarketTime')
        
        if market_price is None:
            market_price = data.get('previousClose')
            
        if market_price is None:
             raise ValueError(f"Yahoo Finance: '{symbol}'의 유효한 시장 가격을 찾을 수 없습니다.")
        
        warning_msg = ""
        if nav_price is None:
             warning_msg = "⚠️ NAV 데이터 누락! 괴리율 계산 불가."
             
        return market_price, nav_price, market_time, warning_msg 
    except Exception as e:
        raise RuntimeError(f"KRX 골드 ETF 가격 및 NAV 조회 실패: {type(e).__name__} - {e}")

# 2. Yahoo Finance 가격 조회
def get_yahoo_price(symbol):
    try:
        ticker = yf.Ticker(symbol)
        data = ticker.info
        price = data.get('regularMarketPrice')
        if price is None:
            price = data.get('previousClose')
        if price is None:
            raise ValueError(f"Yahoo Finance: '{symbol}'에 대한 가격 데이터가 누락되었습니다.")
        return price
    except Exception as e:
        raise RuntimeError(f"Yahoo Finance '{symbol}' 데이터 조회 실패: {type(e).__name__} - {e}")

# 3. 모든 데이터 가져오기
def get_gold_and_fx_data():
    usd_krw = get_yahoo_price("USDKRW=X")
    gold_usd = get_yahoo_price("GC=F")
    market_price, nav_price, market_time, warning_msg = get_korean_gold_data() 
    return market_price, nav_price, usd_krw, gold_usd, market_time, warning_msg

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

def calc_premium():
    market_price, nav_price, usd_krw, gold_usd, market_time, warning_msg = get_gold_and_fx_data()
    
    premium = None
    if nav_price is not None:
        premium = (market_price / nav_price - 1) * 100 
    
    return {
        "korean": market_price,
        "international_krw": nav_price if nav_price is not None else market_price,
        "usd_krw": usd_krw,
        "gold_usd": gold_usd,
        "premium": premium,
        "market_time": market_time,
        "warning_msg": warning_msg 
    }

def create_graph(history):
    history = history[-7:]
    if len(history) < 2: return None
    dates = [x["date"] for x in history]
    premiums = [x["premium"] for x in history]

    plt.figure(figsize=(6, 3))
    plt.plot(dates, premiums, marker="o")
    plt.title("ETF 괴리율 7일 추세 (%)")
    plt.ylabel("괴리율(%)")
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
        return "AI 분석 오류: OpenAI 클라이언트 초기화 실패 (API 키 누락)"
    
    prompt = f"""
다음은 최근 7일간의 ACE KRX금현물 ETF 괴리율 데이터입니다.
{json.dumps(history[-7:], ensure_ascii=False, indent=2)}

오늘의 주요 데이터:
{today_msg}

이 데이터를 기반으로 ACE KRX금현물 ETF의 괴리율(프리미엄) 상승/하락 원인과 간단한 투자 관점 요약을 3줄 이내로 설명해줘.
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

# ---------- 메인 로직 (UnboundLocalError 수정) ----------
def main():
    try:
        today = datetime.date.today().isoformat()
        
        # ⚠️ [수정 1] 변수 미리 초기화 (UnboundLocalError 방지)
        current_premium = None
        change = 0.0
        time_str = ""
        level = "N/A"
        trend = "N/A"
        avg7 = 0.0
        
        info = calc_premium()
        history = load_history()
        
        current_premium = info["premium"]
        final_timestamp = info["market_time"]
        
        # 1. 괴리율 계산 실패 (NAV 누락)
        if current_premium is None:
            
            if history:
                last_valid_data = history[-1]
                last_valid_premium = last_valid_data["premium"]
                
                info["premium"] = last_valid_premium
                
                last_time = last_valid_data.get("time_kst", last_valid_data["date"])
                time_str = f"과거 ({last_time})"
                
                change = 0.0
                last7 = [x["premium"] for x in history[-7:]]
                avg7 = sum(last7)/len(last7) if last7 else 0
                level = "고평가" if info["premium"] > avg7 else "저평가"
                trend = "--- (과거 기록)"
                
                info["warning_msg"] = (
                    f"{info['warning_msg']} - 과거 기록된 괴리율 ({last_valid_premium:.2f}%) 표시됨."
                )
            else:
                # 히스토리 없음 (초기 실행 + NAV 누락)
                info["premium"] = 0.0
                change = 0.0
                avg7 = 0.0 # 초기화
                time_str = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S KST')
                level = "N/A"
                trend = "N/A"
                info["warning_msg"] = (
                    f"{info['warning_msg']} - 기록된 데이터가 없어 괴리율 0.00%로 표시됨."
                )
                
        # 2. 괴리율 계산 성공
        else:
            time_str = timestamp_to_kst(info["market_time"])
            
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

            prev_premium_data = [h for h in history if h["date"] != today]
            prev = prev_premium_data[-1]["premium"] if prev_premium_data else info["premium"]
            change = info["premium"] - prev
            
            last7 = [x["premium"] for x in history[-7:]]
            avg7 = sum(last7)/len(last7) if last7 else 0
            level = "고평가" if info["premium"] > avg7 else "저평가"
            trend = "📈 상승세" if change > 0 else "📉 하락세"
            
        # 텔레그램 메시지 구성
        msg_data = (
            f"📅 {today} ACE KRX금현물 ETF 괴리율 알림\n"
            f"기준 일시: {time_str}\n"
            f"{info['warning_msg']}\n"
            f"국내 ETF 시장가 (주당): {info['korean']:,.0f}원\n"
            f"국제 금 1g 이론가 (NAV): {info['international_krw']:,.0f}원\n"
            f"국제 금시세 (oz): ${info['gold_usd']:,.2f}\n"
            f"환율: {info['usd_krw']:,.2f}원/$\n"
            f"👉 ETF 괴리율: {info['premium']:+.2f}% ({change:+.2f}% vs 전일)\n"
            f"최근 7일 평균 대비: {level} ({avg7:.2f}%) {trend}"
        )
        
        ai_summary = analyze_with_ai(msg_data, history)
        full_msg = f"{msg_data}\n\n🤖 AI 요약:\n{ai_summary}"

        send_telegram_text(full_msg)

        graph_buf = create_graph(history)
        if graph_buf:
            send_telegram_photo(graph_buf, caption="📈 최근 7일 ETF 괴리율 추세")

    except Exception as e:
        try:
            # 오류 내용 전송 (traceback 포함)
            error_msg = f"🔥 치명적인 오류 발생: {type(e).__name__} - {e}\n\n{traceback.format_exc()}"
            send_telegram_text(error_msg[:4000])
        except Exception as telegram_error:
            print(f"ERROR: 최종 오류 알림 발송 실패: {telegram_error}")
            print(f"Original Exception: {e}")

if __name__ == "__main__":
    main()
