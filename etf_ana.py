import streamlit as st
import requests
from bs4 import BeautifulSoup
import pandas as pd
import time
import random
import io
import gspread
from oauth2client.service_account import ServiceAccountCredentials

# --- 網頁設定 ---
st.set_page_config(page_title="台股 ETF 資產管家 (雲端版)", layout="wide")

# --- 連線 Google Sheets 設定 ---
SCOPE = ['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive']

@st.cache_resource
def init_connection():
    try:
        creds_dict = dict(st.secrets["gcp_service_account"])
        creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, SCOPE)
        client = gspread.authorize(creds)
        return client
    except Exception as e:
        st.error(f"連線失敗: {e}")
        return None

def normalize_code(code):
    """
    標準化代號修正版：
    台灣 ETF 慣例通常是 00 開頭。
    如果使用者輸入 50 -> 補成 0050
    如果使用者輸入 735 -> 補成 00735
    """
    code_str = str(code).strip().replace("'", "")
    
    # 如果是純數字，且不是 0 開頭，強制補兩個 0
    if code_str.isdigit() and not code_str.startswith("0"):
        return "00" + code_str
    
    # 或是如果是 4 碼以下但有少 0，用舊邏輯保護
    if code_str.isdigit() and len(code_str) < 4:
        return code_str.zfill(4)
        
    return code_str

def get_google_sheet_data(client):
    try:
        sheet = client.open("ETF_Database").worksheet("holdings")
        data = sheet.get_all_records()
        df = pd.DataFrame(data)
        
        if not df.empty and '代號' in df.columns:
            df['代號'] = df['代號'].apply(normalize_code)
            
        return df
    except Exception as e:
        return pd.DataFrame(columns=["帳號", "代號", "成交均價", "股數"])

def save_to_google_sheet(client, username, code, cost, qty):
    try:
        sheet = client.open("ETF_Database").worksheet("holdings")
        fmt_code = normalize_code(code)
        # 這裡不加單引號，讓 Sheet 自己判斷，反正讀出來我們會 normalize
        sheet.append_row([username, fmt_code, cost, qty])
        return True
    except Exception as e:
        st.error(f"寫入失敗: {e}")
        return False

def delete_from_google_sheet(client, username, code):
    try:
        sheet = client.open("ETF_Database").worksheet("holdings")
        data = sheet.get_all_records()
        df = pd.DataFrame(data)
        
        sheet.clear()
        sheet.append_row(["帳號", "代號", "成交均價", "股數"])
        
        keep_rows = []
        target_code = normalize_code(code)
        deleted = False
        
        for i, row in df.iterrows():
            row_code = normalize_code(row['代號'])
            
            if str(row['帳號']) == str(username) and row_code == target_code and not deleted:
                deleted = True
                continue
            
            row_data = row.tolist()
            row_data[1] = row_code 
            keep_rows.append(row_data)
            
        if keep_rows:
            sheet.append_rows(keep_rows)
        return True
    except Exception as e:
        st.error(f"刪除失敗: {e}")
        return False

client = init_connection()

headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
}

# --- 樣式設定 ---
def style_pl_color(val):
    if isinstance(val, (int, float)):
        color = '#d63031' if val > 0 else '#00b894' if val < 0 else 'black'
        return f'color: {color}; font-weight: bold;'
    return ''

def style_top3_rows(row):
    if row.name in [1, 2, 3]:
        return ['background-color: #ffe6e6'] * len(row)
    return [''] * len(row)

# --- 爬蟲核心 ---
def get_etf_return(stock_code):
    url = f"https://histock.tw/stock/{stock_code}"
    try:
        response = requests.get(url, headers=headers)
        soup = BeautifulSoup(response.text, 'html.parser')
        
        data = {
            '代號': stock_code, '名稱': "未知", '市場別': "未知", '現價': 0.0,
            '一季%': 0.0, '半年%': 0.0, '一年%': 0.0, '綜合平均%': 0.0
        }

        name_tag = soup.find('h3') 
        if name_tag: data['名稱'] = name_tag.text.split('(')[0].strip()

        # 抓取市場別
        candidates = soup.find_all(['li', 'td'])
        for tag in candidates:
            text = tag.text.strip()
            if '市場' in text:
                if '上市' in text: data['市場別'] = '上市'; break
                elif '上櫃' in text: data['市場別'] = '上櫃'; break
        if data['市場別'] == "未知":
            if soup.find(string="上市"): data['市場別'] = '上市'
            elif soup.find(string="上櫃"): data['市場別'] = '上櫃'

        # 抓取現價
        price_span = soup.find('span', id='Price1_lbTPrice')
        if price_span:
            try:
                data['現價'] = float(price_span.text.replace(',', ''))
            except: pass
        else:
            backup_span = soup.find('span', class_='price')
            if backup_span:
                try:
                    data['現價'] = float(backup_span.text.replace(',', ''))
                except: pass

        table = soup.find('table', class_='tbPerform')
        if table:
            target_periods = {'一季': '一季%', '半年': '半年%', '一年': '一年%'}
            rows = table.find_all('tr')
            periods_data = {}
            for row in rows:
                th = row.find('th')
                td = row.find('td')
                if th and td:
                    p_name = th.text.strip()
                    if p_name in target_periods:
                        val_span = td.find('span')
                        if val_span:
                            try:
                                val_str = val_span.text.replace('%', '').replace('+', '').replace(',', '').strip()
                                periods_data[p_name] = float(val_str)
                            except: pass
            
            data['一季%'] = periods_data.get('一季', 0)
            data['半年%'] = periods_data.get('半年', 0)
            data['一年%'] = periods_data.get('一年', 0)

            valid_values = [v for k, v in periods_data.items() if v is not None]
            if valid_values:
                data['綜合平均%'] = round(sum(valid_values) / len(valid_values), 2)
            
        return data
    except: pass
    return None

@st.cache_data(ttl=3600, show_spinner="正在更新 ETF 資料中，請稍候...")
def fetch_all_etf_data():
    url = "https://histock.tw/stock/etf.aspx"
    try:
        response = requests.get(url, headers=headers)
        soup = BeautifulSoup(response.text, 'html.parser')
        
        etf_codes = []
        rows = soup.find_all('tr')
        china_keywords = ['中國', '上證', '滬', '深', '恒生', 'A50', '香港', '港股']

        for row in rows:
            link = row.find('a', href=True)
            if not link or '/stock/' not in link['href']: continue
            href_code = link['href'].split('/')[-1]
            row_text = row.text.strip()
            
            # 這裡只要是數字就抓，透過後續標準化處理
            if not href_code[0].isdigit(): continue
            if href_code.upper().endswith(('L', 'R')): continue 
            if any(kw in row_text for kw in china_keywords): continue 
            if href_code not in etf_codes: etf_codes.append(href_code)

        results = []
        progress_bar = st.progress(0)
        status_text = st.empty()
        
        for i, code in enumerate(etf_codes):
            status_text.text(f"🚀 正在分析 [{i+1}/{len(etf_codes)}]: {code} ...")
            progress_bar.progress((i + 1) / len(etf_codes))
            data = get_etf_return(code)
            if data: results.append(data)
            time.sleep(0.05)
        
        status_text.empty()
        progress_bar.empty()
        return pd.DataFrame(results)

    except Exception as e:
        st.error(f"資料抓取失敗: {e}")
        return pd.DataFrame()

# --- 登入系統 ---
def check_password():
    if "password_correct" not in st.session_state:
        st.sidebar.header("🔒 會員登入")
        st.sidebar.text_input("帳號", key="username")
        st.sidebar.text_input("密碼", type="password", key="password")
        if st.sidebar.button("登入"):
            if st.session_state["username"] == "bobi" and st.session_state["password"] == "1269":
                st.session_state["password_correct"] = True
                st.session_state["current_user"] = "admin"
                st.rerun()
            else:
                st.sidebar.error("帳號或密碼錯誤")
        return False
    elif st.session_state["password_correct"]:
        st.sidebar.success(f"✅ 已登入：{st.session_state['current_user']}")
        if st.sidebar.button("登出"):
            del st.session_state["password_correct"]
            st.rerun()
        return True
    return False

# --- 主程式 ---

is_logged_in = check_password()
current_user = st.session_state.get("current_user", "guest")

st.title("💰 台股 ETF 資產管家 (雲端版)")

df_final = fetch_all_etf_data()

if not df_final.empty:
    tab1, tab2 = st.tabs(["📊 市場排行榜", "💼 我的持股"])
    
    with tab1:
        st.subheader("全台 ETF 績效排行")
        col1, col2 = st.columns([8, 1])
        with col2:
            if st.button('🔄 更新'): st.cache_data.clear(); st.rerun()
        
        # ★ 修正點：把 '市場別' 加回來了
        market_cols = ['代號', '名稱', '市場別', '現價', '一季%', '半年%', '一年%', '綜合平均%']
        
        # 確保欄位存在才顯示
        existing_cols = [c for c in market_cols if c in df_final.columns]
        df_show = df_final[existing_cols].sort_values(by='綜合平均%', ascending=False).reset_index(drop=True)
        df_show.
