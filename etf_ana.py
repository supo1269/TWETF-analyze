import streamlit as st
import requests
from bs4 import BeautifulSoup
import pandas as pd
import time
import random
import io

# --- 網頁設定 ---
st.set_page_config(page_title="台股 ETF 績效自動分析", layout="wide")

# --- 標題與更新按鈕區塊 ---
col1, col2 = st.columns([8, 1])

with col1:
    st.title("📊 台股 ETF 績效分析排行榜")
    st.caption("資料來源：HiStock | 自動過濾：槓桿、反向、中國/港股市場 | 自動判斷：上市/上櫃")

with col2:
    if st.button('🔄 更新'):
        st.cache_data.clear()
        st.rerun()

headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
}

# --- 功能：分析單一檔 ETF (修正市場判斷邏輯) ---
def get_etf_return(stock_code):
    url = f"https://histock.tw/stock/{stock_code}"
    try:
        response = requests.get(url, headers=headers)
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # 1. 初始化資料
        data = {
            '代號': stock_code, 
            '名稱': "未知", 
            '市場別': "未知", 
            '一季%': None, 
            '半年%': None, 
            '一年%': None, 
            '綜合平均%': None
        }

        # 2. 抓取名稱
        name_tag = soup.find('h3') 
        if name_tag:
            data['名稱'] = name_tag.text.split('(')[0].strip()

        # 3. ★ 修正後的市場判斷邏輯 (暴力搜尋法) ★
        # HiStock 的市場資訊通常藏在 li 或 td 裡面
        # 我們直接找包含 "市場" 的元素
        market_found = False
        
        # 搜尋所有的 list item (li) 和 table cell (td)
        candidates = soup.find_all(['li', 'td'])
        
        for tag in candidates:
            text = tag.text.strip()
            # 如果這行文字包含 "市場"，且包含 "上市" 或 "上櫃"
            if '市場' in text:
                if '上市' in text:
                    data['市場別'] = '上市'
                    market_found = True
                    break
                elif '上櫃' in text:
                    data['市場別'] = '上櫃'
                    market_found = True
                    break
        
        # 如果還是沒找到，試試看有沒有單獨的 "上市" 或 "上櫃" 標籤 (備用方案)
        if not market_found:
            if soup.find(string="上市"): data['市場別'] = '上市'
            elif soup.find(string="上櫃"): data['市場別'] = '上櫃'

        # 4. 抓取報酬率表格
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
                        val_str = val_span.text.replace('%', '').replace('+', '').replace(',', '').strip()
                        try:
                            periods_data[p_name] = float(val_str)
                        except: pass
        
        data['一季%'] = periods_data.get('一季')
        data['半年%'] = periods_data.get('半年')
        data['一年%'] = periods_data.get('一年')

        if data['一季%'] is not None and data['半年%'] is not None and data['一年%'] is not None:
            avg = (data['一季%'] + data['半年%'] + data['一年%']) / 3
            data['綜合平均%'] = round(avg, 2)
            return data
            
    except Exception as e:
        pass
    return None

# --- ★ 核心功能：抓取與分析 ---
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
            
            if href_code not in etf_codes: 
                etf_codes.append(href_code)

        results = []
        total = len(etf_codes)
        
        progress_bar = st.progress(0)
        status_text = st.empty()
        
        for i, code in enumerate(etf_codes):
            status_text.text(f"🚀 正在分析 [{i+1}/{total}]: {code} ...")
            progress_bar.progress((i + 1) / total)
            
            data = get_etf_return(code)
            if data:
                results.append(data)
            time.sleep(0.05)
        
        status_text.empty()
        progress_bar.empty()
        
        return pd.DataFrame(results)

    except Exception as e:
        st.error(f"資料抓取失敗: {e}")
        return pd.DataFrame()

# --- 網頁執行流程 ---

df_final = fetch_all_etf_data()

if not df_final.empty:
    cols = ['代號', '名稱', '市場別', '一季%', '半年%', '一年%', '綜合平均%']
    existing_cols = [c for c in cols if c in df_final.columns]
    df_sorted = df_final[existing_cols].sort_values(by='綜合平均%', ascending=False).reset_index(drop=True)
    
    st.success(f"✅ 資料載入成功！共分析 {len(df_sorted)} 檔 ETF。")
    
    st.dataframe(df_sorted, use_container_width=True)
    
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df_sorted.to_excel(writer, index=False)
    
    st.download_button(
        label="📥 下載 Excel 分析報表",
        data=output.getvalue(),
        file_name="ETF_Analysis_Report.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )

else:
    st.warning("目前沒有抓到資料，請點擊右上角的「更新」按鈕重試。")
