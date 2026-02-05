import streamlit as st
import requests
from bs4 import BeautifulSoup
import pandas as pd
import time
import random
import io

# --- 網頁標題與介紹 ---
st.set_page_config(page_title="台股 ETF 績效分析工具", layout="wide")
st.title("📊 台股 ETF 自動篩選與分析")
st.write("本工具會自動抓取 HiStock 的 ETF 清單，過濾掉槓桿、反向與中國市場 ETF，並計算近一季、半年及一年的綜合報酬率。")

headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
}

# --- 功能 1：抓取清單 ---
def get_etf_list():
    url = "https://histock.tw/stock/etf.aspx"
    try:
        response = requests.get(url, headers=headers)
        soup = BeautifulSoup(response.text, 'html.parser')
        etf_codes = []
        rows = soup.find_all('tr')
        china_keywords = ['中國', '上證', '滬', '深', '恒生', 'A50', '香港', '港股']

        for row in rows:
            link = row.find('a', href=True)
            if not link or '/stock/' not in link['href']:
                continue
            href_code = link['href'].split('/')[-1]
            row_text = row.text.strip().replace('\n', ' ')

            if len(href_code) < 4 or len(href_code) > 6 or not href_code[0].isdigit():
                continue
            
            upper_code = href_code.upper()
            if upper_code.endswith(('L', 'R')): 
                continue

            is_china_etf = False
            for kw in china_keywords:
                if kw in row_text:
                    is_china_etf = True
                    break
            if is_china_etf:
                continue

            if href_code not in etf_codes:
                etf_codes.append(href_code)
        return etf_codes
    except Exception as e:
        st.error(f"抓取清單失敗: {e}")
        return []

# --- 功能 2：分析績效 ---
def get_etf_return(stock_code):
    url = f"https://histock.tw/stock/{stock_code}"
    try:
        response = requests.get(url, headers=headers)
        soup = BeautifulSoup(response.text, 'html.parser')
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
            name_tag = soup.find('h3') 
            stock_name = name_tag.text.split('(')[0].strip() if name_tag else "未知"
            return {
                '代號': stock_code, '名稱': stock_name,
                '一季%': target_periods['一季'], '半年%': target_periods['半年'],
                '一年%': target_periods['一年'], '綜合平均%': round(avg_return, 2)
            }
    except: pass
    return None

# --- 主程式介面 ---

if st.button('🚀 開始執行全台 ETF 分析'):
    all_etfs = get_etf_list()
    
    if all_etfs:
        total = len(all_etfs)
        st.info(f"成功篩選出 {total} 檔 ETF，開始分析績效...")
        
        # 建立進度條
        progress_bar = st.progress(0)
        status_text = st.empty()
        
        results = []
        for i, code in enumerate(all_etfs):
            status_text.text(f"正在分析 [{i+1}/{total}]: {code}")
            progress_bar.progress((i + 1) / total)
            
            data = get_etf_return(code)
            if data:
                results.append(data)
            time.sleep(random.uniform(0.1, 0.2)) 

        if results:
            df = pd.DataFrame(results)
            df = df.sort_values(by='綜合平均%', ascending=False)
            
            st.success("✅ 分析完成！")
            
            # 顯示結果表格
            st.write("### 績效排行榜 (依綜合平均排序)")
            st.dataframe(df, use_container_width=True)
            
            # --- 下載按鈕 (Excel 格式) ---
            output = io.BytesIO()
            with pd.ExcelWriter(output, engine='openpyxl') as writer:
                df.to_excel(writer, index=False)
            
            st.download_button(
                label="📥 下載 Excel 分析報表",
                data=output.getvalue(),
                file_name="ETF_績效分析結果.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )
        else:
            st.warning("無法抓取到任何績效數據。")
    else:
        st.error("清單抓取失敗或清單為空。")
