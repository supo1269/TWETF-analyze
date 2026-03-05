import streamlit as st
import requests
from bs4 import BeautifulSoup
import pandas as pd
import time
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import plotly.express as px

# --- 網頁設定 ---
st.set_page_config(page_title="台股 ETF 資產管家 (隱私升級版)", layout="wide")

# --- 連線 Google Sheets 設定 ---
SCOPE = ['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive']

@st.cache_resource
def init_connection():
    try:
        creds_dict = dict(st.secrets["gcp_service_account"])
        creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, SCOPE)
        client = gspread.authorize(creds)
        return client, creds_dict.get("client_email", "您的機器人信箱")
    except Exception as e:
        st.error(f"連線失敗，請檢查 Secrets 設定: {e}")
        return None, ""

client, bot_email = init_connection()
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

# --- 會員系統 ---
def login_user(username, password):
    try:
        sheet = client.open("ETF_Database").worksheet("users")
        users = sheet.get_all_records()
        for u in users:
            if str(u.get('username')) == username and str(u.get('password')) == password:
                return {"success": True, "sheet_url": str(u.get('sheet_url'))}
        return {"success": False, "msg": "帳號或密碼錯誤"}
    except Exception as e:
        return {"success": False, "msg": f"資料庫讀取失敗: {e}"}

def register_user(username, password, sheet_url):
    try:
        sheet = client.open("ETF_Database").worksheet("users")
        users = sheet.get_all_records()
        if any(str(u.get('username')) == username for u in users):
            return "exists"
        try:
            test_open = client.open_by_url(sheet_url)
        except gspread.exceptions.APIError:
            return "no_permission"
        except Exception:
            return "invalid_url"
        sheet.append_row([username, password, sheet_url])
        return "success"
    except Exception as e:
        return f"error: {e}"

# --- 讀寫個人專屬資料庫 ---
def get_personal_sheet_data(sheet_url):
    try:
        sheet = client.open_by_url(sheet_url).sheet1
        data = sheet.get_all_records()
        df = pd.DataFrame(data)
        if not df.empty and '代號' in df.columns:
            df['代號'] = df['代號'].apply(normalize_code)
        return df
    except:
        return pd.DataFrame(columns=["代號", "成交均價", "股數"])

def save_to_personal_sheet(sheet_url, code, cost, qty):
    try:
        sheet = client.open_by_url(sheet_url).sheet1
        if not sheet.get_all_values():
            sheet.append_row(['代號', '成交均價', '股數'])
        sheet.append_row([code, cost, qty])
        return True
    except: return False

def update_personal_sheet_batch(sheet_url, new_df):
    try:
        sheet = client.open_by_url(sheet_url).sheet1
        final_df = new_df[['代號', '成交均價', '股數']].copy()
        final_df['代號'] = final_df['代號'].astype(str).apply(normalize_code)
        
        sheet.clear()
        sheet.append_row(['代號', '成交均價', '股數'])
        if not final_df.empty:
            sheet.append_rows(final_df.values.tolist())
        return True
    except Exception as e:
        st.error(f"更新失敗: {e}")
        return False

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
        if data['市場別'] == "未知":
            if soup.find(string="上市"): data['市場別'] = '上市'
            elif soup.find(string="上櫃"): data['市場別'] = '上櫃'

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
            name_text = link.text.strip()
            option_str = f"{href_code} {name_text}"
            if option_str not in etf_options: etf_options.append(option_str)
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

# --- 自動登入處理 ---
if "logged_in" not in st.session_state:
    st.session_state["logged_in"] = False

if not st.session_state["logged_in"] and "user" in st.query_params:
    auto_user = st.query_params["user"]
    try:
        users_records = client.open("ETF_Database").worksheet("users").get_all_records()
        for u in users_records:
            if str(u.get('username')) == auto_user:
                st.session_state["logged_in"] = True
                st.session_state["current_user"] = auto_user
                st.session_state["sheet_url"] = str(u.get('sheet_url'))
                break
    except: pass

# --- 側邊欄：會員登入/註冊/導覽 ---
with st.sidebar:
    st.header("⚙️ 系統選單")
    
    if not st.session_state["logged_in"]:
        auth_mode = st.radio("會員系統", ["登入", "註冊新帳號"], horizontal=True)
        
        if auth_mode == "登入":
            st.subheader("🔑 登入")
            username = st.text_input("帳號")
            password = st.text_input("密碼", type="password")
            if st.button("登入系統", use_container_width=True):
                if username and password:
                    res = login_user(username, password)
                    if res["success"]:
                        st.session_state["logged_in"] = True
                        st.session_state["current_user"] = username
                        st.session_state["sheet_url"] = res["sheet_url"]
                        st.query_params["user"] = username
                        st.success("登入成功！")
                        time.sleep(1)
                        st.rerun()
                    else:
                        st.error(res["msg"])
                else: st.warning("請輸入帳號密碼")
                
        else:
            st.subheader("📝 註冊")
            st.markdown("為了保障隱私，本系統採用**自帶資料庫**模式。資料將完全儲存在您自己的 Google 雲端硬碟中。")
            with st.expander("👉 點我看綁定教學 (必看)", expanded=True):
                st.markdown(f"""
                1. 請先去 Google Drive 建立一個**全新的空白試算表**。
                2. 點擊右上角「共用」。
                3. 將以下機器人信箱加入，並設為 **「編輯者」**：
                `{bot_email}`
                4. 複製該試算表的網址，貼在下方欄位。
                """)
            new_user = st.text_input("設定帳號")
            new_pass = st.text_input("設定密碼", type="password")
            new_url = st.text_input("貼上您的專屬 Google 試算表網址")
            if st.button("註冊並綁定", use_container_width=True):
                if new_user and new_pass and new_url:
                    with st.spinner("正在驗證試算表權限..."):
                        res = register_user(new_user, new_pass, new_url)
                        if res == "success": st.success("✅ 註冊成功！請切換到「登入」。")
                        elif res == "exists": st.warning("⚠️ 帳號已存在。")
                        elif res == "no_permission": st.error("❌ 機器人無權限！請確認已共用並設為「編輯者」。")
                        elif res == "invalid_url": st.error("❌ 網址錯誤。")
                        else: st.error(f"註冊失敗: {res}")
                else: st.warning("請填寫所有欄位。")
        page = "📊 市場排行榜"
        
    else:
        st.success(f"👋 歡迎，{st.session_state['current_user']}！")
        if st.button("🚪 登出", use_container_width=True):
            st.session_state["logged_in"] = False
            st.session_state.pop("sheet_url", None)
            st.query_params.clear()
            st.rerun()
            
        st.divider()
        page = st.radio("前往頁面", ["💼 我的持股", "📊 市場排行榜"])

# --- 主畫面邏輯 ---
st.title("💰 台股 ETF 資產管家 (隱私升級版)")

if page == "📊 市場排行榜":
    st.subheader("🏆 全台 ETF 績效排行")
    st.info("💡 這裡會抓取全台灣的 ETF 並計算績效，載入時間較長。")
    if st.button('🔄 強制更新行情'): st.cache_data.clear(); st.rerun()
        
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
    if st.session_state["logged_in"]:
        current_user = st.session_state["current_user"]
        my_sheet_url = st.session_state["sheet_url"]
        
        user_df = get_personal_sheet_data(my_sheet_url)
        
        # -----------------------------------------------------
        # 上半部：資產儀表板與持股明細 (只在有資料時顯示)
        # -----------------------------------------------------
        if not user_df.empty:
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
                
                merged_df = merged_df.sort_values(by='代號', ascending=True).reset_index(drop=True)
                
                # --- ★ 全新：左右配置儀表板 ★ ---
                dash_col1, dash_col2 = st.columns([1, 1.5])
                
                # 左邊：總計數字區
                with dash_col1:
                    st.write("### 📊 資產總覽")
                    total_pnl = merged_df['預估損益'].sum()
                    total_value = merged_df['現值'].sum()
                    total_cost = merged_df['總成本'].sum()
                    total_roi = (total_pnl / total_cost * 100) if total_cost > 0 else 0
                    
                    st.metric("總市值", f"${total_value:,.0f}")
                    st.metric("總損益", f"${total_pnl:,.0f}", delta=f"{total_pnl:,.0f}")
                    st.metric("總報酬率", f"{total_roi:.2f}%", delta=f"{total_roi:.2f}%")

                # 右邊：互動式圓餅圖區
                with dash_col2:
                    st.write("### 🍩 資產配置")
                    pie_df = merged_df[merged_df['現值'] > 0]
                    
                    if not pie_df.empty:
                        fig = px.pie(
                            pie_df, 
                            values='現值', 
                            names='名稱', 
                            hole=0.4,
                            hover_data=['代號', '現值'],
                            color_discrete_sequence=px.colors.qualitative.Pastel 
                        )
                        fig.update_traces(textposition='inside', textinfo='percent+label')
                        fig.update_layout(margin=dict(t=10, b=10, l=10, r=10)) # 縮減邊距
                        st.plotly_chart(fig, use_container_width=True)
                        st.caption("💡 提示：點擊右側圖例 (Legend) 可以暫時隱藏/顯示特定 ETF 來重算佔比喔！")
                    else:
                        st.info("請至少選擇一檔有現值的持股來顯示圓餅圖。")

                st.divider()
                
                # --- 持股明細表格 ---
                st.write("### 📄 持股明細")
                view_cols = ['代號', '名稱', '股數', '成交均價', '現價', '現值', '預估損益', '報酬率%']
                view_styler = merged_df[view_cols].style.format({
                    '成交均價': "{:.2f}", '現價': "{:.2f}", '現值': "{:,.0f}",
                    '預估損益': "{:,.0f}", '報酬率%': "{:.2f}%"
                }).map(style_pl_color, subset=['預估損益', '報酬率%'])
                
                st.dataframe(view_styler, use_container_width=True)
                
            else: st.info("目前無有效報價資料。")
        else: 
            st.info("您目前尚未建立任何持股。可以從下方管理區新增！")

        # -----------------------------------------------------
        # 下半部：持股管理區 (新增 / 編輯 / 刪除)
        # -----------------------------------------------------
        st.divider()
        st.write("### ⚙️ 持股管理")
        
        fast_etf_options = get_fast_etf_list()
        
        # 1. 新增持股區
        with st.expander("➕ 新增持股", expanded=False):
            c1, c2, c3, c4 = st.columns([2, 2, 2, 1])
            selected_etf = c1.selectbox("選擇 ETF", options=fast_etf_options, index=None, placeholder="請搜尋...")
            new_cost = c2.number_input("成交均價", min_value=0.0)
            new_qty = c3.number_input("股數", min_value=1, step=1)
            
            if c4.button("儲存新持股"):
                if selected_etf and new_qty > 0:
                    code_to_save = selected_etf.split(" ")[0]
                    if save_to_personal_sheet(my_sheet_url, code_to_save, new_cost, new_qty):
                        st.success(f"已新增 {code_to_save}！")
                        time.sleep(1); st.rerun()
                    else: st.error("儲存失敗，請檢查權限。")
                else: st.warning("資料不完整")

        # 2. 編輯與刪除持股區
        if not user_df.empty and 'merged_df' in locals():
            with st.expander("🛠️ 編輯 / 刪除現有持股", expanded=False):
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
                    if update_personal_sheet_batch(my_sheet_url, df_to_save):
                        st.success("✅ 更新成功！")
                        time.sleep(1); st.rerun()
                    else: st.error("存檔失敗。")

    else: st.warning("請先從左側選單登入系統。")
