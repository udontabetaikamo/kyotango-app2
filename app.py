import streamlit as st
import os
import json
import time
import random
import sqlite3
import pandas as pd
from datetime import datetime
import io
import re
import ast
from streamlit_folium import st_folium
import folium
from geopy.geocoders import Nominatim
from geopy.exc import GeocoderTimedOut
import google.generativeai as genai

# --- Page Config ---
st.set_page_config(
    page_title="Kyotango Property Platform",
    page_icon="🏠",
    layout="wide",
)

# =========================================================================
# 🔐 認証情報をここに直書きします（ファイル読み込みエラー回避の最終手段）
# =========================================================================
CLIENT_CONFIG = {
  "installed": {  # "web" ではなく "installed" (Desktopアプリ) として扱います
    "client_id": "518109148856-ndtiiuuh4tqt0v2jnu92iemmi8734d6d.apps.googleusercontent.com",
    "project_id": "kyotango-app",
    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
    "token_uri": "https://oauth2.googleapis.com/token",
    "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
    "client_secret": "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",
    "redirect_uris": ["urn:ietf:wg:oauth:2.0:oob", "http://localhost"]
  }
}
# =========================================================================

# Google Drive Imports
try:
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaIoBaseUpload
    DRIVE_ENABLED = True
except ImportError:
    DRIVE_ENABLED = False

# --- Custom CSS ---
st.markdown(
    """
    <style>
    .stApp { background-color: #F5F5DC; color: #1D263B; font-family: serif; }
    h1, h2, h3 { color: #1D263B !important; }
    [data-testid="stSidebar"] { background-color: #E8E4D9; border-right: 1px solid #1D263B; }
    .stButton > button { background-color: #1D263B; color: #F5F5DC; border-radius: 4px; font-weight: bold; }
    .stButton > button:hover { background-color: #2C3E50; color: #FFFFFF; }
    .result-box { border: 2px solid #1D263B; padding: 20px; margin-top: 20px; background-color: #FFFFFF; border-radius: 8px; box-shadow: 5px 5px 0px #1D263B; }
    .rating-s { color: #D4AF37; font-weight: bold; font-size: 2em; }
    .rating-a { color: #1D263B; font-weight: bold; font-size: 2em; }
    .rating-b { color: #555555; font-weight: bold; font-size: 2em; }
    .rating-c { color: #888888; font-weight: bold; font-size: 2em; }
    </style>
    """,
    unsafe_allow_html=True,
)

# --- Database Functions ---
DB_PATH = "real_estate.db"

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS properties (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT, address TEXT, latitude REAL, longitude REAL, price INTEGER,
            features TEXT, rating TEXT, memo TEXT, status TEXT, created_at TEXT,
            renovation_cost INTEGER, roi REAL, details_json TEXT, legal_risks TEXT
        )
    ''')
    cols = [("renovation_cost", "INTEGER"), ("roi", "REAL"), ("details_json", "TEXT"), ("legal_risks", "TEXT")]
    for col, type_ in cols:
        try: c.execute(f"ALTER TABLE properties ADD COLUMN {col} {type_}")
        except: pass
    conn.commit()
    conn.close()

def save_property(data):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''
        INSERT INTO properties (
            title, address, latitude, longitude, price, features, rating, memo, status, created_at,
            renovation_cost, roi, details_json, legal_risks
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (
        data['title'], data['address'], data['latitude'], data['longitude'], 
        data['price'], data['features'], data['rating'], data['memo'], 
        data['status'], datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        data.get('renovation_cost', 0), data.get('roi', 0.0), data.get('details_json', '{}'),
        data.get('legal_risks', '')
    ))
    conn.commit()
    new_id = c.lastrowid
    conn.close()
    return new_id

def get_all_properties():
    conn = sqlite3.connect(DB_PATH)
    df = pd.read_sql_query("SELECT * FROM properties ORDER BY created_at DESC", conn)
    conn.close()
    return df

def update_property(id, field, value):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(f"UPDATE properties SET {field} = ? WHERE id = ?", (value, id))
    conn.commit()
    conn.close()

def delete_property(id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("DELETE FROM properties WHERE id = ?", (id,))
    conn.commit()
    conn.close()

init_db()

# --- Google Drive Functions ---
SCOPES = ['https://www.googleapis.com/auth/drive.file']

def get_drive_service_from_session():
    if "credentials" in st.session_state and st.session_state.credentials:
        return build('drive', 'v3', credentials=st.session_state.credentials)
    return None

def get_or_create_folder(service, folder_name, parent_id=None):
    query = f"mimeType='application/vnd.google-apps.folder' and name='{folder_name}' and trashed=false"
    if parent_id:
        query += f" and '{parent_id}' in parents"
    results = service.files().list(q=query, fields="nextPageToken, files(id, name)").execute()
    items = results.get('files', [])
    if not items:
        file_metadata = {'name': folder_name, 'mimeType': 'application/vnd.google-apps.folder'}
        if parent_id: file_metadata['parents'] = [parent_id]
        file = service.files().create(body=file_metadata, fields='id').execute()
        return file.get('id')
    else:
        return items[0]['id']

def upload_file_to_drive(file_obj, filename, property_address):
    try:
        service = get_drive_service_from_session()
        if not service: return "Credentials not found."
        root_id = get_or_create_folder(service, "Kyotango Property Platform")
        prop_folder_id = get_or_create_folder(service, property_address, parent_id=root_id)
        file_metadata = {'name': filename, 'parents': [prop_folder_id]}
        media = MediaIoBaseUpload(file_obj, mimetype=file_obj.type, resumable=True)
        file = service.files().create(body=file_metadata, media_body=media, fields='id').execute()
        return f"Uploaded: {file.get('id')}"
    except Exception as e:
        return f"Upload Failed: {str(e)}"

# --- Logic Functions ---
def get_address_from_coords(lat, lon):
    geolocator = Nominatim(user_agent="kyotango_scouter")
    try:
        location = geolocator.reverse((lat, lon), language='ja', timeout=10)
        if location: return location.address
    except: pass
    return "住所不明"

def get_coords_from_address(address):
    try:
        geolocator = Nominatim(user_agent="kyotango_scouter")
        # Strategy 1: Exact
        search_query = address if "京都" in address else f"京都府 {address}"
        location = geolocator.geocode(search_query, timeout=10)
        if location: return location.latitude, location.longitude, "exact"
        # Strategy 2: Town
        town_address = re.sub(r'[0-9０-９]+', '', address)
        town_address = re.sub(r'[-－番地]+$', '', town_address)
        if town_address and town_address != address:
            search_query = town_address if "京都" in town_address else f"京都府 {town_address}"
            location = geolocator.geocode(search_query, timeout=10)
            if location: return location.latitude, location.longitude, "town"
    except: pass
    return 35.62, 135.06, "city"

def analyze_investment_value(api_key, address, audio_file=None, extra_files=None, current_details=None):
    try:
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel("gemini-flash-latest")
        prompt = f"あなたは不動産投資のプロです。以下の住所と資料から京丹後市での民泊事業価値を分析してください。\n住所: {address}"
        content_parts = [prompt]
        
        if audio_file:
            audio_file.seek(0)
            content_parts.append({"mime_type": "audio/wav", "data": audio_file.read()})
        if extra_files:
            for f in extra_files:
                f.seek(0)
                content_parts.append({"mime_type": f.type, "data": f.read()})
        if current_details:
             prompt += f"\n現在のデータ: {json.dumps(current_details, ensure_ascii=False)}"

        prompt += """
        以下のJSONのみを出力してください:
        {
          "price_listing": "売出価格(万円)", "renovation_estimate": "リノベ費用(万円)", "total_investment": "総額(万円)",
          "expected_revenue_monthly": "想定月商(万円)", "roi_estimate": "表面利回り(%)", "legal_risks": "法的リスク",
          "grade": "総合判定(S/A/B/C)", "bitter_advice": "辛口アドバイス", "pros": "良い点", "cons": "懸念点", "features_summary": "要約"
        }
        """
        content_parts.append(prompt)
        response = model.generate_content(content_parts, generation_config={"response_mime_type": "application/json"})
        return json.loads(response.text.replace("```json", "").replace("```", "").strip())
    except Exception as e:
        return {"error": str(e)}

# --- Session State ---
if "address_val" not in st.session_state: st.session_state.address_val = ""
if "map_center" not in st.session_state: st.session_state.map_center = [35.62, 135.06]
if "analysis_result" not in st.session_state: st.session_state.analysis_result = None
if "view_mode" not in st.session_state: st.session_state.view_mode = "list"
if "selected_property_id" not in st.session_state: st.session_state.selected_property_id = None
if "last_geocoded_address" not in st.session_state: st.session_state.last_geocoded_address = ""

# --- Sidebar ---
with st.sidebar:
    st.header("設定")
    default_api_key = st.secrets.get("GEMINI_API_KEY", "")
    api_key = st.text_input("API Key", value=default_api_key, type="password")
    
    st.markdown("---")
    if DRIVE_ENABLED and CLIENT_CONFIG:
        st.success("✅ Google Drive連携可能")
    else:
        st.error("⚠️ Drive連携エラー")
    
    st.info("Kyotango Property Platform v3.6 (Manual Auth)")
    
    if "credentials" in st.session_state and st.session_state.credentials:
        if st.button("🚪 ログアウト", use_container_width=True):
            st.session_state.credentials = None
            if os.path.exists('token.json'): os.remove('token.json')
            st.rerun()

# --- Login Logic (Manual Copy-Paste Flow) ---
def check_login():
    if st.session_state.get("credentials") and st.session_state.credentials.valid: return True
    if os.path.exists('token.json'):
        try:
            creds = Credentials.from_authorized_user_file('token.json', SCOPES)
            if creds and creds.valid:
                st.session_state.credentials = creds
                return True
            elif creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
                st.session_state.credentials = creds
                with open('token.json', 'w') as token: token.write(creds.to_json())
                return True
        except: pass
    return False

def login_ui():
    st.title("Kyotango Property Platform")
    st.info("👋 Googleアカウントでログインしてください")
    
    # 手動コピー＆ペースト認証フロー
    try:
        flow = InstalledAppFlow.from_client_config(
            CLIENT_CONFIG, 
            SCOPES,
            redirect_uri='urn:ietf:wg:oauth:2.0:oob' # この魔法のURLがエラーを防ぎます
        )
        
        auth_url, _ = flow.authorization_url(prompt='consent')
        
        st.markdown(f"### 手順：")
        st.markdown("1. 下のボタンを押してGoogle認証ページを開いてください。")
        st.link_button("👉 Google認証ページを開く", auth_url)
        st.markdown("2. ログインして許可すると、画面に**長い認証コード**が表示されます。")
        st.markdown("3. そのコードをコピーして、下の入力欄に貼り付けてください。")
        
        code = st.text_input("認証コードをここに貼り付け:")
        
        if st.button("ログイン完了"):
            if code:
                try:
                    flow.fetch_token(code=code)
                    st.session_state.credentials = flow.credentials
                    with open('token.json', 'w') as token:
                        token.write(flow.credentials.to_json())
                    st.rerun()
                except Exception as e:
                    st.error(f"認証失敗: コードが間違っているか、有効期限切れです。\nエラー: {e}")
            else:
                st.warning("コードを入力してください")
    except Exception as e:
        st.error(f"システムエラー: {e}")

if not check_login():
    login_ui()
    st.stop()

# --- Main Application ---
st.title("Kyotango Property Platform")
st.caption(f"Login: {st.session_state.credentials.client_id[:10]}...")

tab_scout, tab_manage, tab_chat = st.tabs(["🔍 目利き", "📂 物件台帳", "💬 経営会議"])

with tab_scout:
    st.subheader("Step 1: 住所・エリア入力")
    address_input = st.text_input("住所を入力 (例：京丹後市網野町...)", value=st.session_state.address_val)
    
    if address_input:
        st.session_state.address_val = address_input
        if address_input != st.session_state.last_geocoded_address:
            coords = get_coords_from_address(address_input)
            lat, lon, precision = coords
            st.session_state.map_center = [lat, lon]
            if precision == "exact": st.success("📍 座標取得成功")
            else: st.warning("⚠️ 詳細位置は地図で調整してください")
            st.session_state.last_geocoded_address = address_input

        current_lat, current_lon = st.session_state.map_center
        m = folium.Map(location=[current_lat, current_lon], zoom_start=18, tiles='Esri.WorldImagery', attr='Esri', height=300)
        folium.Marker([current_lat, current_lon], icon=folium.Icon(color="red")).add_to(m)
        
        map_data = st_folium(m, width="100%", height=300, returned_objects=["last_clicked"])
        if map_data and map_data.get("last_clicked"):
            st.session_state.map_center = [map_data["last_clicked"]["lat"], map_data["last_clicked"]["lng"]]
            st.rerun()

        st.subheader("Step 2: 音声・写真")
        col1, col2 = st.columns(2)
        with col1: audio_in = st.audio_input("マイク")
        with col2: audio_up = st.file_uploader("音声ファイル", type=["mp3", "wav", "m4a"])
        img_up = st.file_uploader("写真", type=['png', 'jpg'], accept_multiple_files=True)
        
        audio = audio_in or audio_up
        if audio and api_key:
            if st.button("AI解析開始"):
                with st.spinner("分析中..."):
                    res = analyze_investment_value(api_key, address_input, audio, img_up)
                    if "error" in res: st.error(res['error'])
                    else: st.session_state.analysis_result = res
                    
                    # Drive Backup
                    if DRIVE_ENABLED:
                        audio.seek(0)
                        upload_file_to_drive(audio, f"audio_{int(time.time())}.wav", address_input)

    if st.session_state.analysis_result:
        res = st.session_state.analysis_result
        st.divider()
        c1, c2, c3 = st.columns(3)
        c1.metric("総合判定", res.get('grade'), delta=res.get('roi_estimate'))
        c2.metric("総投資額", f"{res.get('total_investment')}万")
        c3.metric("表面利回り", f"{res.get('roi_estimate')}%")
        st.error(f"辛口アドバイス: {res.get('bitter_advice')}")
        
        if st.button("💾 保存"):
            save_data = {
                "title": f"{datetime.now().strftime('%Y%m%d')}_{address_input}",
                "address": address_input,
                "latitude": st.session_state.map_center[0],
                "longitude": st.session_state.map_center[1],
                "price": res.get('price_listing', 0),
                "features": res.get('features_summary', ''),
                "rating": res.get('grade'),
                "memo": res.get('bitter_advice'),
                "status": "検討中",
                "renovation_cost": res.get('renovation_estimate', 0),
                "roi": res.get('roi_estimate', 0),
                "details_json": json.dumps(res, ensure_ascii=False),
                "legal_risks": res.get('legal_risks')
            }
            nid = save_property(save_data)
            if img_up:
                d = f"data/images/{nid}"
                os.makedirs(d, exist_ok=True)
                for i in img_up: 
                    with open(os.path.join(d, i.name), "wb") as f: f.write(i.getbuffer())
            st.toast("保存しました！")

with tab_manage:
    st.subheader("📂 物件台帳")
    df = get_all_properties()
    if st.session_state.view_mode == "list":
        if not df.empty:
            # Map without clustering
            df['latitude'] = pd.to_numeric(df['latitude'], errors='coerce')
            valid_df = df[df['latitude'].notna()]
            
            if not valid_df.empty:
                center = [valid_df['latitude'].mean(), valid_df['longitude'].mean()]
                m_port = folium.Map(location=center, zoom_start=12, tiles='CartoDB positron')
                all_coords = []
                for _, row in valid_df.iterrows():
                    color = "red" if row['status']=="購入済み" else "blue"
                    # Add marker directly to map (No Cluster)
                    folium.Marker(
                        [row['latitude'], row['longitude']], 
                        popup=row['title'], 
                        icon=folium.Icon(color=color)
                    ).add_to(m_port)
                    all_coords.append([row['latitude'], row['longitude']])
                
                if all_coords: m_port.fit_bounds(all_coords)
                st_folium(m_port, height=400, width="100%")

            st.dataframe(df[["id", "status", "title", "price", "roi", "rating"]], use_container_width=True)
            
            sel_id = st.selectbox("詳細を見る物件ID", df['id'])
            if st.button("詳細へ"):
                st.session_state.selected_property_id = sel_id
                st.session_state.view_mode = "detail"
                st.rerun()
                
    elif st.session_state.view_mode == "detail":
        if st.button("⬅️ 戻る"):
            st.session_state.view_mode = "list"
            st.rerun()
        
        row = df[df['id'] == st.session_state.selected_property_id].iloc[0]
        st.header(row['title'])
        st.info(f"メモ: {row['memo']}")
        
        new_stat = st.selectbox("ステータス", ["検討中", "購入済み", "見送り"], index=["検討中", "購入済み", "見送り"].index(row['status']) if row['status'] in ["検討中", "購入済み", "見送り"] else 0)
        if st.button("ステータス更新"):
            update_property(row['id'], "status", new_stat)
            st.rerun()
            
        # Images
        img_dir = f"data/images/{row['id']}"
        if os.path.exists(img_dir):
            imgs = os.listdir(img_dir)
            if imgs:
                cols = st.columns(3)
                for idx, im in enumerate(imgs):
                    cols[idx%3].image(os.path.join(img_dir, im))

with tab_chat:
    st.subheader("💬 経営会議")
    if "messages" not in st.session_state: st.session_state.messages = []
    for m in st.session_state.messages: st.chat_message(m["role"]).write(m["content"])
    
    if p := st.chat_input("相談内容..."):
        st.session_state.messages.append({"role": "user", "content": p})
        st.chat_message("user").write(p)
        
        if api_key:
            model = genai.GenerativeModel("gemini-flash-latest")
            ctx = f"物件データ: {get_all_properties().to_string()}"
            res = model.generate_content(f"あなたは不動産コンサルタントです。\n{ctx}\n\n質問: {p}")
            st.session_state.messages.append({"role": "assistant", "content": res.text})
            st.chat_message("assistant").write(res.text)
