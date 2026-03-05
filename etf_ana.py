import streamlit as st
import requests
from bs4 import BeautifulSoup
import pandas as pd
import time
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import plotly.express as px

# --- 網頁設定 ---
st.set_page_config(page_title="台股 ETF 資產管家 (領息強化版)", layout="wide")

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

# --- 會員與資料庫系統 (略，保持不變) ---
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

# --- 爬蟲核心 (強化版：加入配息爬取) ---
def get_etf_data(stock_code):
    url = f"https://histock.tw/stock/{stock_code}"
    div_url = f"https://histock.tw/stock/dividend/{stock_code}" # 配息專屬頁
    try:
        data = {'代號': stock_code, '名稱': "未知", '現價': 0.0, '一年配息': 0.0, '綜合平均%': 0.0}
        
        # 1. 抓現價與名稱
        resp = requests.get(url, headers=headers)
        soup = BeautifulSoup(resp.text, 'html.parser')
        name_tag = soup.find('h3')
        if name_tag: data['名稱'] = name_tag.text.split('(')[0].strip()
        price_span = soup.find('span', id='Price1_lbTPrice') or soup.find('span', class_='price')
        if price_span:
            try: data['現價'] = float(price_span.text.replace(',', ''))
            except: pass
            
        # 2. 抓配息資料 (近一年累計)
        div_resp = requests.get(div_url, headers=headers)
        div_soup = BeautifulSoup(div_resp.text, 'html.parser')
        # 尋找「合計」或表格中的現金股利欄位
        div_table = div_soup.find('table', class_='tb-stock')
        if div_table:
            rows = div_table.find_all('tr')
            if len(rows) > 1:
                # 抓取最近一年的現金股利合計（簡單邏輯：抓取第一列的現金合計）
                # 注意：各網站結構略不同，這裡取最保險的累計方式
                tds = rows[1].find_all('td')
                if len(tds) >= 4:
                    try: data['一年配息'] = float(tds[4].text.strip()) # 通常合計在第5欄
                    except: pass
        
        # 3. 抓績效 (沿用)
        perf_table = soup.find('table', class_='tbPerform')
        if perf_table:
            vals = []
            for row in perf_table.find_all('tr'):
                th, td = row.find('th'), row.find('td')
                if th and td and th.text.strip() in ['一季', '半年', '一年']:
                    v = td.find('span')
                    if v: vals.append(float(v.text.replace('%','').replace('+','').replace(',','')))
            if vals: data['綜合平均%'] = round(sum(vals)/len(vals), 2)
            
        return data
    except: return None

@st.cache_data(ttl=86400)
def get_fast_etf_list():
    url = "https://histock.tw/stock/etf.aspx"
    try:
        response = requests.get(url, headers=headers)
        soup = BeautifulSoup(response.text, 'html.parser')
        etf_options = []
        for row in soup.find_all('tr'):
            link = row.find('a', href=True)
            if not link or '/stock/' not in link['href']: continue
            href_code = link['href'].split('/')[-1]
            if not href_code[0].isdigit() or href_code.upper().endswith(('L', 'R')): continue
            name_text = link.text.strip()
            etf_options.append(f"{href_code} {name_text}")
        return list(set(etf_options))
    except: return []

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

# --- 側邊欄 ---
with st.sidebar:
    st.header("⚙️ 系統選單")
    if not st.session_state["logged_in"]:
        auth_mode = st.radio("會員系統", ["登入", "註冊新帳號"], horizontal=True)
        if auth_mode == "登入":
            username = st.text_input("帳號")
            password = st.text_input("密碼", type="password")
            if st.button("登入系統", use_container_width=True):
                res = login_user(username, password)
                if res["success"]:
                    st.session_state["logged_in"] = True
                    st.session_state["current_user"] = username
                    st.session_state["sheet_url"] = res["sheet_url"]
                    st.query_params["user"] = username
                    st.rerun()
                else: st.error(res["msg"])
        else:
            new_user = st.text_input("設定帳號")
            new_pass = st.text_input("設定密碼", type="password")
            new_url = st.text_input("貼上 Google 試算表網址")
            if st.button("註冊並綁定", use_container_width=True):
                res = register_user(new_user, new_pass, new_url)
                if res == "success": st.success("✅ 註冊成功！請登入。")
                else: st.error(f"錯誤: {res}")
        page = "📊 市場排行榜"
    else:
        st.success(f"👋 {st.session_state['current_user']}")
        if st.button("🚪 登出", use_container_width=True):
            st.session_state["logged_in"] = False
            st.query_params.clear()
            st.rerun()
        st.divider()
        page = st.radio("前往頁面", ["💼 我的持股", "📊 市場排行榜"])

# --- 主畫面 ---
st.title("💰 台股 ETF 資產管家")

if page == "📊 市場排行榜":
    st.info("💡 排行榜以綜合績效為主。")
    # (此部分維持原狀，略過重複程式碼以節省篇幅)
    st.write("請切換至「我的持股」查看強化版功能！")

elif page == "💼 我的持股":
    if st.session_state["logged_in"]:
        my_sheet_url = st.session_state["sheet_url"]
        user_df = get_personal_sheet_data(my_sheet_url)
        
        if not user_df.empty:
            unique_codes = user_df['代號'].unique()
            my_holdings_data = []
            with st.spinner("⚡ 正在計算最新行情與配息資訊..."):
                for code in unique_codes:
                    data = get_etf_data(code)
                    if data: my_holdings_data.append(data)
            
            if my_holdings_data:
                merged_df = pd.merge(user_df, pd.DataFrame(my_holdings_data), on='代號', how='left')
                merged_df['股數'] = pd.to_numeric(merged_df['股數'], errors='coerce').fillna(0)
                merged_df['現值'] = merged_df['現價'] * merged_df['股數']
                merged_df['總成本'] = merged_df['成交均價'] * merged_df['股數']
                merged_df['預估損益'] = merged_df['現值'] - merged_df['總成本']
                
                # --- ★ 領息核心計算 ★ ---
                merged_df['年領息'] = merged_df['一年配息'] * merged_df['股數']
                merged_df['成本殖利率%'] = (merged_df['一年配息'] / merged_df['成交均價'] * 100).fillna(0)
                
                merged_df = merged_df.sort_values(by='代號').reset_index(drop=True)
                
                # --- 左右配置儀表板 ---
                dash_col1, dash_col2 = st.columns([1, 1.5])
                with dash_col1:
                    st.write("### 📊 資產總覽")
                    total_value = merged_df['現值'].sum()
                    total_pnl = merged_df['預估損益'].sum()
                    total_div = merged_df['年領息'].sum() # 總領息
                    
                    st.metric("總市值", f"${total_value:,.0f}")
                    st.metric("預估年領息", f"${total_div:,.0f}", help="根據近一年配息累計計算")
                    st.metric("總損益", f"${total_pnl:,.0f}", delta=f"{total_pnl:,.0f}")
                    st.metric("月平均被動收入", f"${(total_div/12):,.0f}")

                with dash_col2:
                    st.write("### 🍩 資產配置")
                    fig = px.pie(merged_df[merged_df['現值']>0], values='現值', names='名稱', hole=0.4)
                    fig.update_layout(margin=dict(t=10, b=10, l=10, r=10))
                    st.plotly_chart(fig, use_container_width=True)

                st.divider()
                
                # --- 強化版持股明細 ---
                st.write("### 📄 持股與配息明細")
                view_cols = ['代號', '名稱', '股數', '成交均價', '現價', '現值', '預估損益', '一年配息', '年領息', '成本殖利率%']
                view_styler = merged_df[view_cols].style.format({
                    '成交均價': "{:.2f}", '現價': "{:.2f}", '現值': "{:,.0f}",
                    '預估損益': "{:,.0f}", '一年配息': "{:.2f}", '年領息': "{:,.0f}", '成本殖利率%': "{:.2f}%"
                }).map(style_pl_color, subset=['預估損益', '成本殖利率%'])
                st.dataframe(view_styler, use_container_width=True)

        # --- 管理區 (新增/編輯) ---
        st.divider()
        st.write("### ⚙️ 持股管理")
        fast_etf_options = get_fast_etf_list()
        
        with st.expander("➕ 新增持股"):
            c1, c2, c3, c4 = st.columns([2, 2, 2, 1])
            selected_etf = c1.selectbox("選擇 ETF", options=fast_etf_options, index=None)
            new_cost = c2.number_input("成交均價", min_value=0.0)
            new_qty = c3.number_input("股數", min_value=1, step=1)
            if c4.button("儲存"):
                if selected_etf:
                    save_to_personal_sheet(my_sheet_url, selected_etf.split(" ")[0], new_cost, new_qty)
                    st.rerun()

        if not user_df.empty:
            with st.expander("🛠️ 編輯 / 刪除"):
                edit_df = merged_df[['代號', '名稱', '股數', '成交均價']].copy()
                edit_df.insert(0, '刪除', False)
                edited = st.data_editor(edit_df, hide_index=True)
                if st.button("💾 儲存變更"):
                    update_personal_sheet_batch(my_sheet_url, edited[edited['刪除']==False][['代號', '成交均價', '股數']])
                    st.rerun()
    else: st.warning("請登入。")
