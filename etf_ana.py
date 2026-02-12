import streamlit as st
import requests
from bs4 import BeautifulSoup
import pandas as pd
import time
import random
import io
import os

# --- 網頁設定 ---
st.set_page_config(page_title="台股 ETF 資產管家", layout="wide")

# --- 模擬資料庫路徑 ---
CSV_FILE = "holdings.csv"

# --- 檢查帳本是否存在，不存在就創一個 ---
if not os.path.exists(CSV_FILE):
    df_empty = pd.DataFrame(columns=["代號", "成本", "股數"])
    df_empty.to_csv(CSV_FILE, index=False)

# --- 樣式設定 ---
def style_text_color(val):
    if isinstance(val, (int, float)):
        color = '#d63031' if val > 0 else '#00b894' if val < 0 else 'black'
        return f'color: {color}; font-weight: bold;'
    return ''

def style_top3_rows(row):
    if row.name in [1, 2, 3]:
        return ['background-color: #ffe6e6'] * len(row)
    return [''] * len(row)

# --- 爬蟲功能 (維持不變) ---
headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
}

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

# --- ★ 登入系統邏輯 ★ ---
def check_password():
    """簡單的密碼驗證"""
    def password_entered():
        if st.session_state["username"] == "admin" and st.session_state["password"] == "1234":
            st.session_state["password_correct"] = True
            del st.session_state["password"]  # 安全起見，刪除密碼
        else:
            st.session_state["password_correct"] = False

    if "password_correct" not in st.session_state:
        # 顯示登入框
        st.sidebar.header("🔒 會員登入")
        st.sidebar.text_input("帳號", key="username")
        st.sidebar.text_input("密碼", type="password", key="password")
        st.sidebar.button("登入", on_click=password_entered)
        return False
    elif not st.session_state["password_correct"]:
        # 登入失敗
        st.sidebar.header("🔒 會員登入")
        st.sidebar.text_input("帳號", key="username")
        st.sidebar.text_input("密碼", type="password", key="password")
        st.sidebar.button("登入", on_click=password_entered)
        st.sidebar.error("帳號或密碼錯誤")
        return False
    else:
        # 登入成功
        st.sidebar.success("✅ 已登入：admin")
        if st.sidebar.button("登出"):
            st.session_state["password_correct"] = False
            st.rerun()
        return True

# --- 存檔功能 ---
def save_holding(code, cost, qty):
    try:
        df = pd.read_csv(CSV_FILE, dtype=str) # 讀取舊資料
        new_row = pd.DataFrame({"代號": [code], "成本": [cost], "股數": [qty]})
        df = pd.concat([df, new_row], ignore_index=True)
        df.to_csv(CSV_FILE, index=False)
        return True
    except Exception as e:
        st.error(f"存檔失敗: {e}")
        return False

def delete_holding(index):
    try:
        df = pd.read_csv(CSV_FILE)
        df = df.drop(index)
        df.to_csv(CSV_FILE, index=False)
        return True
    except: return False

# --- 主程式區塊 ---

is_logged_in = check_password()

st.title("💰 台股 ETF 資產管家")

# 準備資料 (如果有登入就顯示，沒登入也顯示，但功能不同)
df_final = fetch_all_etf_data()

if not df_final.empty:
    
    # 建立分頁
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

    # --- 分頁 2: 我的持股 ---
    with tab2:
        if is_logged_in:
            st.subheader("我的持股管理")
            
            # 1. 新增持股區
            with st.expander("➕ 新增持股"):
                c1, c2, c3, c4 = st.columns([2, 2, 2, 1])
                new_code = c1.text_input("代號 (如 0050)")
                new_cost = c2.number_input("平均成本", min_value=0.0)
                new_qty = c3.number_input("股數", min_value=1, step=1)
                
                if c4.button("儲存"):
                    if new_code and new_qty > 0:
                        if save_holding(new_code, new_cost, new_qty):
                            st.success(f"已新增 {new_code}")
                            time.sleep(1)
                            st.rerun()
                    else:
                        st.warning("請輸入完整資料")

            # 2. 讀取並顯示持股
            if os.path.exists(CSV_FILE):
                my_df = pd.read_csv(CSV_FILE, dtype={'代號': str})
                
                if not my_df.empty:
                    # 合併行情資料
                    # 這裡要做一點資料處理，把爬蟲抓到的行情併進來
                    merged_df = pd.merge(my_df, df_final, on='代號', how='left')
                    
                    # 顯示持股表格 (可以刪除)
                    st.write("目前持股明細：")
                    
                    # 為了讓刪除功能好做，我們用 data_editor (可編輯表格) 或是每一行加按鈕
                    # 這裡示範簡單的列表 + 刪除按鈕
                    for idx, row in merged_df.iterrows():
                        with st.container():
                            c1, c2, c3, c4, c5 = st.columns([1, 2, 2, 2, 1])
                            c1.write(f"**{row['代號']}**")
                            c2.write(f"{row['名稱']}")
                            
                            # 顯示報酬率顏色
                            ret = row['綜合平均%']
                            color = "red" if ret > 0 else "green" if ret < 0 else "black"
                            c3.markdown(f"綜合績效: <span style='color:{color}'>{ret}%</span>", unsafe_allow_html=True)
                            
                            c4.write(f"持有: {row['股數']} 股 (成本 {row['成本']})")
                            
                            if c5.button("刪除", key=f"del_{idx}"):
                                delete_holding(idx)
                                st.rerun()
                            st.divider()
                            
                else:
                    st.info("目前還沒有持股，請上方新增。")
            else:
                st.info("資料庫初始化中...")
        else:
            st.warning("🔒 請先從左側登入，才能查看與管理持股。")

else:
    st.warning("資料載入中，請稍候...")
