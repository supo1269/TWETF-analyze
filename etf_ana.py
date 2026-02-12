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
    """初始化 Google Sheets 連線"""
    try:
        # 從 Streamlit Secrets 讀取憑證
        creds_dict = dict(st.secrets["gcp_service_account"])
        creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, SCOPE)
        client = gspread.authorize(creds)
        return client
    except Exception as e:
        st.error(f"連線失敗，請檢查 Secrets 設定: {e}")
        return None

def get_google_sheet_data(client):
    """讀取 Google Sheets 資料"""
    try:
        sheet = client.open("ETF_Database").worksheet("holdings")
        data = sheet.get_all_records() # 讀取所有資料
        return pd.DataFrame(data)
    except Exception as e:
        st.error(f"讀取試算表失敗 (請確認檔名是否為 ETF_Database 且已共用): {e}")
        return pd.DataFrame(columns=["帳號", "代號", "成本", "股數"])

def save_to_google_sheet(client, username, code, cost, qty):
    """寫入資料到 Google Sheets"""
    try:
        sheet = client.open("ETF_Database").worksheet("holdings")
        # 新增一列：帳號, 代號, 成本, 股數
        sheet.append_row([username, code, cost, qty])
        return True
    except Exception as e:
        st.error(f"寫入失敗: {e}")
        return False

def delete_from_google_sheet(client, username, code):
    """刪除資料 (這是最簡單的實作：讀出來->刪掉->寫回去)"""
    try:
        sheet = client.open("ETF_Database").worksheet("holdings")
        data = sheet.get_all_records()
        df = pd.DataFrame(data)
        
        # 找出要刪除的列 (帳號和代號都要符合)
        # 注意：這是簡單版刪除，如果同一支股票買兩次，會刪到第一筆
        # 為了精確刪除，通常需要 ID，但這裡我們先用簡單邏輯
        
        # 重新寫入除了目標之外的所有資料
        # 1. 清空工作表 (保留第一列標題)
        sheet.clear()
        sheet.append_row(["帳號", "代號", "成本", "股數"]) # 寫回標題
        
        # 2. 篩選出不刪除的資料
        keep_rows = []
        deleted = False
        for i, row in df.iterrows():
            # 這裡把數字轉成字串比對比較保險
            if str(row['帳號']) == str(username) and str(row['代號']) == str(code) and not deleted:
                deleted = True # 標記已刪除 (只刪一筆)
                continue
            keep_rows.append(row.tolist())
            
        # 3. 寫回
        if keep_rows:
            sheet.append_rows(keep_rows)
            
        return True
    except Exception as e:
        st.error(f"刪除失敗: {e}")
        return False

# --- 初始化雲端連線 ---
client = init_connection()

# --- 樣式與爬蟲功能 (維持不變) ---
headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
}

def style_text_color(val):
    if isinstance(val, (int, float)):
        color = '#d63031' if val > 0 else '#00b894' if val < 0 else 'black'
        return f'color: {color}; font-weight: bold;'
    return ''

def style_top3_rows(row):
    if row.name in [1, 2, 3]:
        return ['background-color: #ffe6e6'] * len(row)
    return [''] * len(row)

def get_etf_return(stock_code):
    url = f"https://histock.tw/stock/{stock_code}"
    try:
        response = requests.get(url, headers=headers)
        soup = BeautifulSoup(response.text, 'html.parser')
        
        data = {
            '代號': stock_code, '名稱': "未知", '市場別': "未知", 
            '一季%': None, '半年%': None, '一年%': None, '綜合平均%': None
        }

        name_tag = soup.find('h3') 
        if name_tag: data['名稱'] = name_tag.text.split('(')[0].strip()

        candidates = soup.find_all(['li', 'td'])
        for tag in candidates:
            text = tag.text.strip()
            if '市場' in text:
                if '上市' in text: data['市場別'] = '上市'; break
                elif '上櫃' in text: data['市場別'] = '上櫃'; break
        if data['市場別'] == "未知":
            if soup.find(string="上市"): data['市場別'] = '上市'
            elif soup.find(string="上櫃"): data['市場別'] = '上櫃'

        table = soup.find('table', class_='tbPerform')
        if not table: return None
        
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
        
        data['一季%'] = periods_data.get('一季')
        data['半年%'] = periods_data.get('半年')
        data['一年%'] = periods_data.get('一年')

        if data['一季%'] is not None and data['半年%'] is not None and data['一年%'] is not None:
            avg = (data['一季%'] + data['半年%'] + data['一年%']) / 3
            data['綜合平均%'] = round(avg, 2)
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
            
            if len(href_code) < 4 or len(href_code) > 6 or not href_code[0].isdigit(): continue
            if href_code.upper().endswith(('L', 'R')): continue 
            if any(kw in row_text for kw in china_keywords): continue 
            if href_code not in etf_codes: etf_codes.append(href_code)

        results = []
        total = len(etf_codes)
        progress_bar = st.progress(0)
        status_text = st.empty()
        
        for i, code in enumerate(etf_codes):
            status_text.text(f"🚀 正在分析 [{i+1}/{total}]: {code} ...")
            progress_bar.progress((i + 1) / total)
            data = get_etf_return(code)
            if data: results.append(data)
            time.sleep(0.05)
        
        status_text.empty()
        progress_bar.empty()
        return pd.DataFrame(results)

    except Exception as e:
        st.error(f"資料抓取失敗: {e}")
        return pd.DataFrame()

# --- 登入系統邏輯 ---
def check_password():
    def password_entered():
        if st.session_state["username"] == "bobi" and st.session_state["password"] == "supo1269":
            st.session_state["password_correct"] = True
            st.session_state["current_user"] = "admin" # 紀錄當前使用者
            del st.session_state["password"]
        else:
            st.session_state["password_correct"] = False

    if "password_correct" not in st.session_state:
        st.sidebar.header("🔒 會員登入")
        st.sidebar.text_input("帳號", key="username")
        st.sidebar.text_input("密碼", type="password", key="password")
        st.sidebar.button("登入", on_click=password_entered)
        return False
    elif not st.session_state["password_correct"]:
        st.sidebar.header("🔒 會員登入")
        st.sidebar.text_input("帳號", key="username")
        st.sidebar.text_input("密碼", type="password", key="password")
        st.sidebar.button("登入", on_click=password_entered)
        st.sidebar.error("帳號或密碼錯誤")
        return False
    else:
        st.sidebar.success(f"✅ 已登入：{st.session_state['current_user']}")
        if st.sidebar.button("登出"):
            st.session_state["password_correct"] = False
            st.rerun()
        return True

# --- 主程式區塊 ---

is_logged_in = check_password()
current_user = st.session_state.get("current_user", "guest")

st.title("💰 台股 ETF 資產管家 (雲端版)")

df_final = fetch_all_etf_data()

if not df_final.empty:
    
    tab1, tab2 = st.tabs(["📊 市場排行榜", "💼 我的持股"])
    
    # --- 分頁 1: 市場排行榜 ---
    with tab1:
        st.subheader("全台 ETF 績效排行")
        col1, col2 = st.columns([8, 1])
        with col2:
            if st.button('🔄 更新行情'):
                st.cache_data.clear()
                st.rerun()
        
        cols = ['代號', '名稱', '市場別', '一季%', '半年%', '一年%', '綜合平均%']
        existing_cols = [c for c in cols if c in df_final.columns]
        df_sorted = df_final[existing_cols].sort_values(by='綜合平均%', ascending=False).reset_index(drop=True)
        df_sorted.index = df_sorted.index + 1
        
        styler = df_sorted.style.map(style_text_color, subset=['一季%', '半年%', '一年%', '綜合平均%']) \
                                .apply(style_top3_rows, axis=1) \
                                .format("{:.2f}", subset=['一季%', '半年%', '一年%', '綜合平均%'])
        
        st.dataframe(styler, use_container_width=True, height=600)

    # --- 分頁 2: 我的持股 (串接 Google Sheets) ---
    with tab2:
        if is_logged_in and client:
            st.subheader(f"{current_user} 的持股管理")
            
            # 1. 新增持股區
            with st.expander("➕ 新增持股"):
                c1, c2, c3, c4 = st.columns([2, 2, 2, 1])
                new_code = c1.text_input("代號 (如 0050)")
                new_cost = c2.number_input("平均成本", min_value=0.0)
                new_qty = c3.number_input("股數", min_value=1, step=1)
                
                if c4.button("儲存"):
                    if new_code and new_qty > 0:
                        if save_to_google_sheet(client, current_user, new_code, new_cost, new_qty):
                            st.success(f"已儲存 {new_code} 到雲端！")
                            time.sleep(1)
                            st.rerun()
                    else:
                        st.warning("請輸入完整資料")

            # 2. 讀取並顯示持股
            my_df = get_google_sheet_data(client)
            
            if not my_df.empty:
                # 確保代號是字串，方便合併
                my_df['代號'] = my_df['代號'].astype(str)
                
                # ★ 只顯示當前登入使用者的資料
                user_df = my_df[my_df['帳號'] == current_user].copy()
                
                if not user_df.empty:
                    merged_df = pd.merge(user_df, df_final, on='代號', how='left')
                    
                    st.write("目前持股明細 (資料已同步至 Google Sheets)：")
                    
                    for idx, row in merged_df.iterrows():
                        with st.container():
                            c1, c2, c3, c4, c5 = st.columns([1, 2, 2, 2, 1])
                            c1.write(f"**{row['代號']}**")
                            c2.write(f"{row['名稱']}")
                            
                            ret = row['綜合平均%']
                            # 處理 NaN
                            if pd.isna(ret): ret = 0
                                
                            color = "red" if ret > 0 else "green" if ret < 0 else "black"
                            c3.markdown(f"綜合績效: <span style='color:{color}'>{ret}%</span>", unsafe_allow_html=True)
                            
                            c4.write(f"持有: {row['股數']} 股 (成本 {row['成本']})")
                            
                            # 刪除按鈕
                            if c5.button("刪除", key=f"del_{row['代號']}_{idx}"):
                                if delete_from_google_sheet(client, current_user, row['代號']):
                                    st.success("已刪除")
                                    time.sleep(1)
                                    st.rerun()
                            st.divider()
                else:
                    st.info("您目前沒有持股資料。")
            else:
                st.info("資料庫讀取中或為空...")
        elif not is_logged_in:
            st.warning("🔒 請先從左側登入，才能查看與管理持股。")
        else:
            st.error("無法連線到 Google Sheets，請檢查 Secrets 設定。")

else:
    st.warning("資料載入中，請稍候...")
