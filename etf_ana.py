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
    """標準化代號"""
    code_str = str(code).strip().replace("'", "")
    if code_str.isdigit() and not code_str.startswith("0"):
        return "00" + code_str
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
    """單筆新增用"""
    try:
        sheet = client.open("ETF_Database").worksheet("holdings")
        sheet.append_row([username, code, cost, qty])
        return True
    except Exception as e:
        st.error(f"寫入失敗: {e}")
        return False

def update_google_sheet_batch(client, username, new_df):
    """
    ★ 批次更新功能 ★
    直接把編輯好的 DataFrame 寫回 Google Sheets
    """
    try:
        sheet = client.open("ETF_Database").worksheet("holdings")
        
        # 1. 讀取所有資料 (因為試算表裡可能有別人的資料，不能全刪)
        all_data = sheet.get_all_records()
        all_df = pd.DataFrame(all_data)
        
        # 2. 如果試算表是空的，直接寫入
        if all_df.empty:
            final_df = new_df
        else:
            # 3. 保留「不是」當前使用者的資料
            other_users_df = all_df[all_df['帳號'].astype(str) != str(username)]
            
            # 4. 把當前使用者的新資料 (new_df) 加上去
            # new_df 只有代號、成本、股數，我們要補上 '帳號'
            user_data_to_save = new_df.copy()
            user_data_to_save['帳號'] = username
            
            # 5. 合併
            final_df = pd.concat([other_users_df, user_data_to_save], ignore_index=True)
        
        # 6. 整理欄位順序 (確保符合 Google Sheets 格式)
        # 確保代號是字串 (雖然寫入時 gspread 會處理，但保險起見)
        final_df['代號'] = final_df['代號'].astype(str).apply(normalize_code)
        
        # 只取需要的欄位
        final_df = final_df[['帳號', '代號', '成交均價', '股數']]
        
        # 7. 寫回 Google Sheets (清除舊的 -> 寫入新的)
        sheet.clear()
        # 寫入標題
        sheet.append_row(['帳號', '代號', '成交均價', '股數'])
        # 寫入內容 (將 DataFrame 轉為 list of lists)
        sheet.append_rows(final_df.values.tolist())
        
        return True
    except Exception as e:
        st.error(f"更新失敗: {e}")
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

        candidates = soup.find_all(['li', 'td'])
        for tag in candidates:
            text = tag.text.strip()
            if '市場' in text:
                if '上市' in text: data['市場別'] = '上市'; break
                elif '上櫃' in text: data['市場別'] = '上櫃'; break
        if data['市場別'] == "未知":
            if soup.find(string="上市"): data['市場別'] = '上市'
            elif soup.find(string="上櫃"): data['市場別'] = '上櫃'

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
        
        market_cols = ['代號', '名稱', '市場別', '現價', '一季%', '半年%', '一年%', '綜合平均%']
        existing_cols = [c for c in market_cols if c in df_final.columns]
        df_show = df_final[existing_cols].sort_values(by='綜合平均%', ascending=False).reset_index(drop=True)
        df_show.index += 1
        
        styler = df_show.style.map(style_pl_color, subset=['一季%', '半年%', '一年%', '綜合平均%']) \
                              .apply(style_top3_rows, axis=1) \
                              .format("{:.2f}", subset=['現價', '一季%', '半年%', '一年%', '綜合平均%'])
        st.dataframe(styler, use_container_width=True)

    with tab2:
        if is_logged_in and client:
            st.subheader(f"{current_user} 的持股管理")
            
            # --- 新增持股區 (維持不變) ---
            with st.expander("➕ 新增持股", expanded=False):
                c1, c2, c3, c4 = st.columns([2, 2, 2, 1])
                etf_options = []
                for idx, row in df_final.iterrows():
                    code = row.get('代號', '')
                    name = row.get('名稱', '')
                    if code: etf_options.append(f"{code} {name}")
                
                selected_etf = c1.selectbox("選擇或搜尋 ETF", options=etf_options, index=None, placeholder="請輸入代號或名稱...")
                new_cost = c2.number_input("成交均價", min_value=0.0)
                new_qty = c3.number_input("股數", min_value=1, step=1)
                
                if c4.button("儲存"):
                    if selected_etf and new_qty > 0:
                        code_to_save = selected_etf.split(" ")[0]
                        save_to_google_sheet(client, current_user, code_to_save, new_cost, new_qty)
                        st.success(f"已新增 {code_to_save}！")
                        time.sleep(1)
                        st.rerun()
                    else: st.warning("請選擇 ETF 並輸入股數")

            st.divider()

            # --- ★★★ 可編輯表格區 ★★★ ---
            my_df = get_google_sheet_data(client)
            
            if not my_df.empty:
                my_df['代號'] = my_df['代號'].apply(normalize_code)
                df_final['代號'] = df_final['代號'].apply(normalize_code)
                user_df = my_df[my_df['帳號'] == current_user].copy()
                
                if not user_df.empty:
                    merged_df = pd.merge(user_df, df_final, on='代號', how='left')
                    merged_df['現價'] = pd.to_numeric(merged_df['現價'], errors='coerce').fillna(0)
                    merged_df['成交均價'] = pd.to_numeric(merged_df['成交均價'], errors='coerce').fillna(0)
                    merged_df['股數'] = pd.to_numeric(merged_df['股數'], errors='coerce').fillna(0)
                    
                    merged_df['現值'] = merged_df['現價'] * merged_df['股數']
                    merged_df['總成本'] = merged_df['成交均價'] * merged_df['股數']
                    merged_df['預估損益'] = merged_df['現值'] - merged_df['總成本']
                    merged_df['報酬率%'] = 0.0 # 改名方便顯示
                    mask = merged_df['總成本'] > 0
                    merged_df.loc[mask, '報酬率%'] = (merged_df.loc[mask, '預估損益'] / merged_df.loc[mask, '總成本']) * 100
                    
                    # --- 準備編輯的 DataFrame ---
                    # 1. 增加「刪除」勾選欄位 (預設 False)
                    merged_df['刪除'] = False
                    
                    # 2. 選出要顯示和編輯的欄位
                    # 注意：我們把「刪除」放最前面
                    edit_cols = ['刪除', '代號', '名稱', '股數', '成交均價', '現價', '現值', '預估損益', '報酬率%']
                    edit_df = merged_df[edit_cols].copy()
                    
                    st.write("### 💼 持股明細 (可直接編輯)")
                    st.info("💡 提示：點擊標題可排序 | 點擊數字可直接修改成本與股數 | 勾選左側可刪除 | 修改完畢請按下方「儲存變更」")
                    
                    # 3. 使用 st.data_editor
                    edited_df = st.data_editor(
                        edit_df,
                        column_config={
                            "刪除": st.column_config.CheckboxColumn(
                                "刪除?",
                                help="勾選後按儲存即可刪除此筆資料",
                                default=False,
                            ),
                            "代號": st.column_config.TextColumn(
                                "代號", disabled=True # 代號不能改，改了會亂掉
                            ),
                            "名稱": st.column_config.TextColumn(
                                "名稱", disabled=True
                            ),
                            "股數": st.column_config.NumberColumn(
                                "股數", min_value=1, step=1, format="%d"
                            ),
                            "成交均價": st.column_config.NumberColumn(
                                "成交均價", min_value=0.0, format="%.2f"
                            ),
                            "現價": st.column_config.NumberColumn(
                                "現價", disabled=True, format="%.2f"
                            ),
                            "現值": st.column_config.NumberColumn(
                                "現值", disabled=True, format="$%d"
                            ),
                            "預估損益": st.column_config.NumberColumn(
                                "預估損益", disabled=True, format="$%d"
                            ),
                            "報酬率%": st.column_config.NumberColumn(
                                "報酬率%", disabled=True, format="%.2f%%"
                            ),
                        },
                        disabled=["代號", "名稱", "現價", "現值", "預估損益", "報酬率%"], # 再次確保這些不能改
                        hide_index=True,
                        use_container_width=True
                    )
                    
                    # 4. 儲存按鈕邏輯
                    if st.button("💾 儲存變更", type="primary"):
                        # 邏輯：
                        # 1. 找出沒有被勾選刪除的資料
                        # 2. 取出 '代號', '成交均價', '股數' 這三個我們要存的欄位
                        
                        rows_to_save = edited_df[edited_df['刪除'] == False]
                        
                        # 準備寫回資料庫的 DataFrame
                        df_to_save = rows_to_save[['代號', '成交均價', '股數']].copy()
                        
                        if update_google_sheet_batch(client, current_user, df_to_save):
                            st.success("✅ 資料已更新！(修改與刪除已同步)")
                            time.sleep(1)
                            st.rerun()
                        else:
                            st.error("存檔失敗，請檢查網路或權限。")

                else: st.info("尚無持股資料。")
            else: st.info("讀取資料庫中...")
        elif not is_logged_in: st.warning("🔒 請先登入")
        else: st.error("連線錯誤")

else: st.warning("資料載入中...")
