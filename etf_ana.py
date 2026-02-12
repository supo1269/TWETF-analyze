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
            if any(kw in row_text for kw in china_keywords): continue # 排除中國市場
            
            if href_code not in etf_codes: 
                etf_codes.append(href_code)

        # 2. 開始分析
        results = []
        total = len(etf_codes)
        
        # 建立進度條容器
        progress_bar = st.progress(0)
        status_text = st.empty()
        
        for i, code in enumerate(etf_codes):
            # 更新進度文字
            status_text.text(f"🚀 正在分析 [{i+1}/{total}]: {code} ...")
            progress_bar.progress((i + 1) / total)
            
            data = get_etf_return(code)
            if data:
                results.append(data)
            time.sleep(0.05) #稍微加快速度
        
        # 清除進度條
        status_text.empty()
        progress_bar.empty()
        
        return pd.DataFrame(results)

    except Exception as e:
        st.error(f"資料抓取失敗: {e}")
        return pd.DataFrame()

# --- 網頁執行流程 ---

# 呼叫主函式 (如果有快取就讀快取，沒有就重跑)
df_final = fetch_all_etf_data()

if not df_final.empty:
    # 排序：綜合平均由高到低
    df_sorted = df_final.sort_values(by='綜合平均%', ascending=False).reset_index(drop=True)
    
    # 顯示成功訊息 (注意這裡的括號要小心)
    st.success(f"✅ 資料載入成功！共分析 {len(df_sorted)} 檔 ETF。")
    
    # 顯示排行榜表格
    st.dataframe(df_sorted, use_container_width=True)
    
    # 準備 Excel 下載
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df_sorted.to_excel(writer, index=False)
    
    # 下載按鈕 (這就是最容易出錯的地方，請確保複製完整)
    st.download_button(
        label="📥 下載 Excel 分析報表",
        data=output.getvalue(),
        file_name="ETF_Analysis_Report.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )

else:
    st.warning("目前沒有抓到資料，請點擊右上角的「更新」按鈕重試。")
