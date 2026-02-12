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
    st.caption("資料來源：HiStock | 自動過濾：槓桿、反向、中國/港股市場")

with col2:
    # 這裡就是你要的強制更新按鈕
    if st.button('🔄 更新'):
        st.cache_data.clear() # 清除快取
        st.rerun() # 重新執行網頁

headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
}

# --- 功能：分析單一檔 ETF ---
def get_etf_return(stock_code):
    url = f"https://histock.tw/stock/{stock_code}"
    try:
        response = requests.get(url, headers=headers)
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # 找尋績效表格
        table = soup.find('table', class_='tbPerform')
        if not table: return None
        
        target_periods = {'一季': None, '半年': None, '一年': None}
        rows = table.find_all('tr')
        
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
                            target_periods[p_name] = float(val_str)
                        except: continue
                        
        if all(v is not None for v in target_periods.values()):
            avg_return = sum(target_periods.values()) / 3
            # 嘗試抓取名稱
            name_tag = soup.find('h3') 
            stock_name = name_tag.text.split('(')[0].strip() if name_tag else "未知"
            
            return {
                '代號': stock_code, 
                '名稱': stock_name,
                '一季%': target_periods['一季'], 
                '半年%': target_periods['半年'],
                '一年%': target_periods['一年'], 
                '綜合平均%': round(avg_return, 2)
            }
    except: pass
    return None

# --- ★ 核心功能：抓取與分析 (加上快取) ---
# ttl=3600 代表這份資料會被快取 1 小時
@st.cache_data(ttl=3600, show_spinner="正在更新 ETF 資料中，請稍候...")
def fetch_all_etf_data():
    url = "https://histock.tw/stock/etf.aspx"
    try:
        response = requests.get(url, headers=headers)
        soup = BeautifulSoup(response.text, 'html.parser')
        
        etf_codes = []
        rows = soup.find_all('tr')
        china_keywords = ['中國', '上證', '滬', '深', '恒生', 'A50', '香港', '港股']

        # 1. 抓取清單
        for row in rows:
            link = row.find('a', href=True)
            if not link or '/stock/' not in link['href']: continue
            
            href_code = link['href'].split('/')[-1]
            row_text = row.text.strip()
            
            # 過濾邏輯
            if len(href_code) < 4 or len(href_code) > 6 or not href_code[0].isdigit(): continue
            if href_code.upper().endswith(('L', 'R')): continue # 排除槓桿/反向
            if any(
