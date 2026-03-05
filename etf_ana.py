import streamlit as st
import requests
from bs4 import BeautifulSoup
import pandas as pd
import time
import io
import gspread
from oauth2client.service_account import ServiceAccountCredentials

# --- 網頁設定 ---
st.set_page_config(page_title="台股 ETF 資產管家 (極速版)", layout="wide")

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

client = init_connection()
headers = {"User-Agent": "Mozilla/5.0"}

# --- 工具函式 ---
def normalize_code(code):
    code_str = str(code).strip().replace("'", "")
    if code_str.isdigit() and not code_str.startswith("0"):
        return "00" + code_str
    if code_str.isdigit() and len(code_str) < 4:
        return code_str.zfill(4)
    return code_str

def style_pl_color(val):
    if isinstance(val, (int, float)):
        color = '#d63031' if val > 0 else '#00b894' if val < 0 else 'black'
        return f'color: {color}; font-weight: bold;'
    return ''

# --- ★ 會員系統 (讀寫 Google Sheets users 分頁) ★ ---
def login_user(username, password):
    try:
        sheet = client.open("ETF_Database").worksheet("users")
        users = sheet.get_all_records()
        for u in users:
            if str(u.get('username')) == username and str(u.get('password')) == password:
                return True
        return False
    except Exception as e:
        st.error("找不到 users 工作表，請確認 Google Sheets 設定。")
        return False

def register_user(username, password):
    try:
        sheet = client.open("ETF_Database").worksheet("users")
        users = sheet.get_all_records()
        if any(str(u.get('username')) == username for u in users):
            return "exists" # 帳號已存在
        sheet.append_row([username, password])
        return "success"
    except Exception as e:
        st.error("註冊失敗，請確認 users 工作表存在。")
        return "error"

# --- 資料庫操作 (持股) ---
def get_google_sheet_data():
    try:
        sheet = client.open("ETF_Database").worksheet("holdings")
        data = sheet.get_all_records()
        df = pd.DataFrame(data)
        if not df.empty and '代號' in df.columns:
            df['代號'] = df['代號'].apply(normalize_code)
        return df
    except:
        return pd.DataFrame(columns=["帳號", "代號", "成交均價", "股數"])

def save_to_google_sheet(username, code, cost, qty):
    try:
        client.open("ETF_Database").worksheet("holdings").append_row([username, code, cost, qty])
        return True
    except: return False

def update_google_sheet_batch(username, new_df):
    try:
        sheet = client.open("ETF_Database").worksheet("holdings")
        all_df = pd.DataFrame(sheet.get_all_records())
        if all_df.empty:
            final_df = new_df
        else:
            other_users_df = all_df[all_df['帳號'].astype(str) != str(username)]
            user_data_to_save = new_df.copy()
            user_data_to_save['帳號'] = username
            final_df = pd.concat([other_users_df, user_data_to_save], ignore_index=True)
        
        final_df['代號'] = final_df['代號'].astype(str).apply(normalize_code)
        final_df = final_df[['帳號', '代號', '成交均價', '股數']]
        
        sheet.clear()
        sheet.append_row(['帳號', '代號', '成交均價', '股數'])
        sheet.append_rows(final_df.values.tolist())
        return True
    except: return False

# --- 爬蟲核心 ---
def get_etf_return(stock_code):
    url = f"https://histock.tw/stock/{stock_code}"
    try:
        response = requests.get(url, headers=headers)
        soup = BeautifulSoup(response.text, 'html.parser')
        data = {'代號': stock_code, '名稱': "未知", '市場別': "未知", '現價': 0.0, '一季%': 0.0, '半年%': 0.0, '一年%': 0.0, '綜合平均%': 0.0}
        
        name_tag = soup.find('h3') 
        if name_tag: data['名稱'] = name_tag.text.split('(')[0].strip()
            
        for tag in soup.find_all(['li', 'td']):
            text = tag.text.strip()
            if '市場' in text:
                if '上市' in text: data['市場別'] = '上市'; break
                elif '上櫃' in text: data['市場別'] = '上櫃'; break

        price_span = soup.find('span', id='Price1_lbTPrice') or soup.find('span', class_='price')
        if price_span:
            try: data['現價'] = float(price_span.text.replace(',', ''))
            except: pass

        table = soup.find('table', class_='tbPerform')
        if table:
            target_periods = {'一季': '一季%', '半年': '半年%', '一年': '一年%'}
            periods_data = {}
            for row in table.find_all('tr'):
                th, td = row.find('th'), row.find('td')
                if th and td and th.text.strip() in target_periods:
                    val_span = td.find('span')
                    if val_span:
                        try: periods_data[th.text.strip()] = float(val_span.text.replace('%', '').replace('+', '').replace(',', '').strip())
                        except: pass
            
            data['一季%'] = periods_data.get('一季', 0)
            data['半年%'] = periods_data.get('半年', 0)
            data['一年%'] = periods_data.get('一年', 0)
            valid_values = [v for k, v in periods_data.items() if v is not None]
            if valid_values: data['綜合平均%'] = round(sum(valid_values) / len(valid_values), 2)
        return data
    except: return None

# ★ 極速爬蟲：只抓代號跟名稱給選單用 (0.5秒)
@st.cache_data(ttl=86400)
def get_fast_etf_list():
    url = "https://histock.tw/stock/etf.aspx"
    try:
        response = requests.get(url, headers=headers)
        soup = BeautifulSoup(response.text, 'html.parser')
        etf_options = []
        china_keywords = ['中國', '上證', '滬', '深', '恒生', 'A50', '香港', '港股']
        for row in soup.find_all('tr'):
            link = row.find('a', href=True)
            if not link or '/stock/' not in link['href']: continue
            href_code = link['href'].split('/')[-1]
            row_text = row.text.strip()
            if not href_code[0].isdigit() or href_code.upper().endswith(('L', 'R')) or any(kw in row_text for kw in china_keywords): continue
            
            # 從 a 標籤抓取名稱 (HiStock 格式通常是: 0050 元大台灣50)
            name_text = link.text.strip()
            # 確保不會重複
            option_str = f"{href_code} {name_text}"
            if option_str not in etf_options:
                etf_options.append(option_str)
        return etf_options
    except: return []

@st.cache_data(ttl=3600, show_spinner="正在掃描全台 ETF 績效中，請稍候 (約 1-2 分鐘)...")
def fetch_all_etf_data():
    options = get_fast_etf_list()
    results = []
    progress_bar = st.progress(0)
    status_text = st.empty()
    for i, opt in enumerate(options):
        code = opt.split(" ")[0]
        status_text.text(f"🚀 正在分析 [{i+1}/{len(options)}]: {code} ...")
        progress_bar.progress((i + 1) / len(options))
        data = get_etf_return(code)
        if data: results.append(data)
        time.sleep(0.05)
    status_text.empty(); progress_bar.empty()
    return pd.DataFrame(results)

# --- 側邊欄：會員登入/註冊/導覽 ---

if "logged_in" not in st.session_state:
    st.session_state["logged_in"] = False

with st.sidebar:
    st.header("⚙️ 系統選單")
    
    if not st.session_state["logged_in"]:
        # 登入與註冊切換
        auth_mode = st.radio("會員系統", ["登入", "註冊帳號"], horizontal=True)
        
        username = st.text_input("帳號")
        password = st.text_input("密碼", type="password")
        
        if auth_mode == "登入":
            if st.button("🔑 登入系統", use_container_width=True):
                if login_user(username, password):
                    st.session_state["logged_in"] = True
                    st.session_state["current_user"] = username
                    st.success("登入成功！")
                    time.sleep(1)
                    st.rerun()
                else:
                    st.error("帳號或密碼錯誤！")
        else:
            if st.button("📝 註冊新帳號", use_container_width=True):
                if username and password:
                    res = register_user(username, password)
                    if res == "success":
                        st.success("✅ 註冊成功！請切換到「登入」以進入系統。")
                    elif res == "exists":
                        st.warning("⚠️ 帳號已存在，請換一個。")
                else:
                    st.warning("請輸入帳號與密碼。")
                    
        # 沒登入時，強迫停在排行榜 (或只顯示排行榜)
        page = "📊 市場排行榜"
        
    else:
        st.success(f"👋 歡迎回來，{st.session_state['current_user']}！")
        if st.button("🚪 登出", use_container_width=True):
            st.session_state["logged_in"] = False
            st.rerun()
            
        st.divider()
        # ★ 頁面導航選單 ★
        page = st.radio("前往頁面", ["💼 我的持股", "📊 市場排行榜"])

# --- 主畫面邏輯 ---

st.title("💰 台股 ETF 資產管家")

if page == "📊 市場排行榜":
    st.subheader("🏆 全台 ETF 績效排行")
    st.info("💡 這裡會抓取全台灣的 ETF 並計算績效，載入時間較長。")
    
    if st.button('🔄 強制更新行情'):
        st.cache_data.clear()
        st.rerun()
        
    df_final = fetch_all_etf_data()
    if not df_final.empty:
        market_cols = ['代號', '名稱', '市場別', '現價', '一季%', '半年%', '一年%', '綜合平均%']
        existing_cols = [c for c in market_cols if c in df_final.columns]
        df_show = df_final[existing_cols].sort_values(by='綜合平均%', ascending=False).reset_index(drop=True)
        df_show.index += 1
        
        styler = df_show.style.map(style_pl_color, subset=['一季%', '半年%', '一年%', '綜合平均%']) \
                              .format("{:.2f}", subset=['現價', '一季%', '半年%', '一年%', '綜合平均%'])
        st.dataframe(styler, use_container_width=True, height=600)

elif page == "💼 我的持股":
    # 只有登入才能看到
    if st.session_state["logged_in"]:
        current_user = st.session_state["current_user"]
        
        # 1. 抓取極速清單給選單用 (0.5秒完成)
        fast_etf_options = get_fast_etf_list()
        
        with st.expander("➕ 新增持股", expanded=False):
            c1, c2, c3, c4 = st.columns([2, 2, 2, 1])
            selected_etf = c1.selectbox("選擇 ETF", options=fast_etf_options, index=None, placeholder="請搜尋...")
            new_cost = c2.number_input("成交均價", min_value=0.0)
            new_qty = c3.number_input("股數", min_value=1, step=1)
            
            if c4.button("儲存"):
                if selected_etf and new_qty > 0:
                    code_to_save = selected_etf.split(" ")[0]
                    save_to_google_sheet(current_user, code_to_save, new_cost, new_qty)
                    st.success(f"已新增 {code_to_save}！")
                    time.sleep(1)
                    st.rerun()
                else: st.warning("資料不完整")

        # 2. 讀取使用者持股
        my_df = get_google_sheet_data()
        if not my_df.empty:
            user_df = my_df[my_df['帳號'].astype(str) == str(current_user)].copy()
            
            if not user_df.empty:
                # ★ 只抓取使用者「有買」的 ETF 最新價格 (極速載入) ★
                unique_codes = user_df['代號'].unique()
                my_holdings_data = []
                
                with st.spinner("⚡ 正在為您獲取最新持股報價..."):
                    for code in unique_codes:
                        data = get_etf_return(code)
                        if data: my_holdings_data.append(data)
                
                if my_holdings_data:
                    current_prices_df = pd.DataFrame(my_holdings_data)
                    merged_df = pd.merge(user_df, current_prices_df, on='代號', how='left')
                    
                    merged_df['現價'] = pd.to_numeric(merged_df['現價'], errors='coerce').fillna(0)
                    merged_df['股數'] = pd.to_numeric(merged_df['股數'], errors='coerce').fillna(0)
                    merged_df['現值'] = merged_df['現價'] * merged_df['股數']
                    merged_df['總成本'] = merged_df['成交均價'] * merged_df['股數']
                    merged_df['預估損益'] = merged_df['現值'] - merged_df['總成本']
                    merged_df['報酬率%'] = 0.0
                    mask = merged_df['總成本'] > 0
                    merged_df.loc[mask, '報酬率%'] = (merged_df.loc[mask, '預估損益'] / merged_df.loc[mask, '總成本']) * 100
                    
                    # --- 儀表板區 ---
                    total_pnl = merged_df['預估損益'].sum()
                    total_value = merged_df['現值'].sum()
                    total_cost = merged_df['總成本'].sum()
                    total_roi = (total_pnl / total_cost * 100) if total_cost > 0 else 0
                    
                    k1, k2, k3 = st.columns(3)
                    k1.metric("總市值", f"${total_value:,.0f}")
                    k2.metric("總損益", f"${total_pnl:,.0f}", delta=f"{total_pnl:,.0f}")
                    k3.metric("總報酬率", f"{total_roi:.2f}%", delta=f"{total_roi:.2f}%")
                    
                    view_cols = ['代號', '名稱', '股數', '成交均價', '現價', '現值', '預估損益', '報酬率%']
                    view_styler = merged_df[view_cols].style.format({
                        '成交均價': "{:.2f}", '現價': "{:.2f}", '現值': "{:,.0f}",
                        '預估損益': "{:,.0f}", '報酬率%': "{:.2f}%"
                    }).map(style_pl_color, subset=['預估損益', '報酬率%'])
                    
                    st.dataframe(view_styler, use_container_width=True)
                    
                    st.divider()
                    
                    # --- 編輯區 ---
                    with st.expander("🛠️ 編輯/刪除持股", expanded=False):
                        merged_df['刪除'] = False
                        edit_df = merged_df[['刪除', '代號', '名稱', '股數', '成交均價']].copy()
                        
                        edited_df = st.data_editor(
                            edit_df,
                            column_config={
                                "刪除": st.column_config.CheckboxColumn("刪除?", default=False),
                                "代號": st.column_config.TextColumn("代號", disabled=True),
                                "名稱": st.column_config.TextColumn("名稱", disabled=True),
                                "股數": st.column_config.NumberColumn("股數", min_value=1, step=1, format="%d"),
                                "成交均價": st.column_config.NumberColumn("成交均價", min_value=0.0, format="%.2f"),
                            },
                            hide_index=True, use_container_width=True
                        )
                        
                        if st.button("💾 儲存變更", type="primary"):
                            rows_to_save = edited_df[edited_df['刪除'] == False]
                            df_to_save = rows_to_save[['代號', '成交均價', '股數']].copy()
                            if update_google_sheet_batch(current_user, df_to_save):
                                st.success("✅ 更新成功！")
                                time.sleep(1); st.rerun()
                            else: st.error("存檔失敗。")
                else: st.info("目前無有效報價資料。")
            else: st.info("您目前尚未建立任何持股。")
        else: st.info("讀取資料庫中...")
    else:
        st.warning("請先從左側選單登入系統。")
