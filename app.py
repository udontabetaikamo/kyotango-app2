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
from streamlit_folium import st_folium
import folium
# MarkerCluster is intentionally removed to show all pins
from geopy.geocoders import Nominatim
from geopy.exc import GeocoderTimedOut
import google.generativeai as genai

# --- Page Config (Must be first) ---
st.set_page_config(
    page_title="Kyotango Property Platform",
    page_icon="🏠",
    layout="wide",
)

# =============== クラウド公開用：Secretsから認証ファイルを作成 ===============
# これがないと、ネット上でGoogleログイン機能が動きません
if "gcp_service_account" in st.secrets:
    # ファイルが存在しない場合のみ作成（上書き防止）
    if not os.path.exists("credentials.json"):
        with open("credentials.json", "w") as f:
            # Secretsの中身をJSONファイルとして書き出す
            # st.secrets["gcp_service_account"] はTOMLのセクションですが、
            # Streamlitが自動的に辞書としてパースしてくれている場合と、文字列の場合があります。
            # ここでは文字列(JSON文字列)として貼り付けられたケースを想定して処理します。
            try:
                # もしSecretsにJSON文字列としてそのまま貼ってある場合
                secret_str = st.secrets["gcp_service_account"]
                if isinstance(secret_str, str) and secret_str.strip().startswith("{"):
                     f.write(secret_str)
                else:
                     # TOMLテーブルとしてパースされている場合
                     json.dump(dict(st.secrets["gcp_service_account"]), f)
            except Exception as e:
                print(f"Error creating credentials.json: {e}")
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

# --- Custom CSS (Japanese Modern Design) ---
st.markdown(
    """
    <style>
    /* Global Styles */
    .stApp {
        background-color: #F5F5DC; /* Ecru (Generi-iro) */
        color: #1D263B; /* Indigo (Ai-iro) */
        font-family: "Hiragino Mincho ProN", "Yu Mincho", serif;
    }
    
    /* Headers */
    h1, h2, h3 {
        color: #1D263B !important;
        font-family: "Hiragino Mincho ProN", "Yu Mincho", serif;
    }
    
    /* Sidebar */
    [data-testid="stSidebar"] {
        background-color: #E8E4D9;
        border-right: 1px solid #1D263B;
    }
    
    /* Buttons */
    .stButton > button {
        background-color: #1D263B;
        color: #F5F5DC;
        border-radius: 4px;
        border: none;
        font-weight: bold;
    }
    .stButton > button:hover {
        background-color: #2C3E50;
        color: #FFFFFF;
    }
    
    /* Input Fields */
    .stTextInput > div > div > input, .stTextArea > div > div > textarea, .stNumberInput > div > div > input {
        background-color: #FFFFFF;
        color: #1D263B;
        border: 1px solid #1D263B;
    }
    
    /* Result Box */
    .result-box {
        border: 2px solid #1D263B;
        padding: 20px;
        margin-top: 20px;
        background-color: #FFFFFF;
        border-radius: 8px;
        box-shadow: 5px 5px 0px #1D263B;
    }
    
    .rating-s { color: #D4AF37; font-weight: bold; font-size: 2em; }
    .rating-a { color: #1D263B; font-weight: bold; font-size: 2em; }
    .rating-b { color: #555555; font-weight: bold; font-size: 2em; }
    .rating-c { color: #888888; font-weight: bold; font-size: 2em; }
    
    .metric-label { font-size: 0.9em; color: #555; }
    .metric-value { font-size: 1.2em; font-weight: bold; color: #1D263B; }
    
    </style>
    """,
    unsafe_allow_html=True,
)

# --- Database Functions ---
DB_PATH = "real_estate.db"

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    # Create table with new schema if not exists
    c.execute('''
        CREATE TABLE IF NOT EXISTS properties (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT,
            address TEXT,
            latitude REAL,
            longitude REAL,
            price INTEGER,
            features TEXT,
            rating TEXT,
            memo TEXT,
            status TEXT,
            created_at TEXT,
            renovation_cost INTEGER,
            roi REAL,
            details_json TEXT,
            legal_risks TEXT
        )
    ''')
    
    # Migration: Add columns if they don't exist (for existing DBs)
    try: c.execute("ALTER TABLE properties ADD COLUMN renovation_cost INTEGER")
    except sqlite3.OperationalError: pass
    try: c.execute("ALTER TABLE properties ADD COLUMN roi REAL")
    except sqlite3.OperationalError: pass
    try: c.execute("ALTER TABLE properties ADD COLUMN details_json TEXT")
    except sqlite3.OperationalError: pass
    try: c.execute("ALTER TABLE properties ADD COLUMN legal_risks TEXT")
    except sqlite3.OperationalError: pass
    
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

# Initialize DB
init_db()

# --- Google Drive Functions ---
SCOPES = ['https://www.googleapis.com/auth/drive.file']

def get_drive_service():
    creds = None
    if os.path.exists('token.json'):
        creds = Credentials.from_authorized_user_file('token.json', SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if os.path.exists('credentials.json'):
                flow = InstalledAppFlow.from_client_secrets_file('credentials.json', SCOPES)
                creds = flow.run_local_server(port=0)
                with open('token.json', 'w') as token:
                    token.write(creds.to_json())
            else:
                return None
    return build('drive', 'v3', credentials=creds)

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
        file_metadata = {
            'name': folder_name,
            'mimeType': 'application/vnd.google-apps.folder'
        }
        if parent_id:
            file_metadata['parents'] = [parent_id]
        file = service.files().create(body=file_metadata, fields='id').execute()
        return file.get('id')
    else:
        return items[0]['id']

def upload_file_to_drive(file_obj, filename, property_address):
    try:
        service = get_drive_service_from_session() # Use session service
        if not service:
            return "Credentials not found."
        
        # 1. Get/Create Root Folder
        root_id = get_or_create_folder(service, "Kyotango Property Platform")
        
        # 2. Get/Create Property Folder
        prop_folder_id = get_or_create_folder(service, property_address, parent_id=root_id)
        
        # 3. Upload File
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
        return "住所不明"
    except: return "住所を取得できませんでした"

def get_coords_from_address(address):
    try:
        print(f"DEBUG: Geocoding address: {address}")
        geolocator = Nominatim(user_agent="kyotango_scouter")
        
        # Strategy 1: Exact Search
        try:
            search_query = address
            if "京都" not in address:
                search_query = f"京都府 {address}"
            
            location = geolocator.geocode(search_query, timeout=10)
            if location: return location.latitude, location.longitude, "exact"
        except Exception as e:
            print(f"DEBUG: Strategy 1 failed: {e}")

        # Strategy 2: Fallback (Remove numbers for Town level search)
        try:
            # Remove numbers and full-width numbers
            town_address = re.sub(r'[0-9０-９]+', '', address)
            # Remove trailing hyphens or "番地" if left
            town_address = re.sub(r'[-－番地]+$', '', town_address)
            
            if town_address and town_address != address:
                search_query = town_address
                if "京都" not in town_address:
                    search_query = f"京都府 {town_address}"
                
                location = geolocator.geocode(search_query, timeout=10)
                if location: return location.latitude, location.longitude, "town"
        except Exception as e:
            print(f"DEBUG: Strategy 2 failed: {e}")
        
        # Strategy 3: City Fallback (Kyotango City Hall)
        print("DEBUG: Fallback to City Hall")
        return 35.62, 135.06, "city"
        
    except Exception as e:
        print(f"CRITICAL ERROR in get_coords_from_address: {e}")
        return 35.62, 135.06, "city"

def analyze_investment_value(api_key, address, audio_file=None, extra_files=None, current_details=None):
    """
    Deep Analysis using Gemini 1.5 Flash.
    """
    try:
        genai.configure(api_key=api_key)
        model_name = "gemini-flash-latest" # Or "gemini-1.5-flash" depending on availability
        try:
            model = genai.GenerativeModel(model_name)
        except: pass

        prompt = f"""
        あなたは不動産投資のプロフェッショナルです。
        以下の住所と資料から、京丹後市での古民家民泊事業としての投資価値を厳しく分析してください。
        
        【物件住所】
        {address}
        """
        
        content_parts = [prompt]
        
        if audio_file:
            prompt += "\n【音声メモ】\n(音声ファイルの内容)"
            audio_file.seek(0)
            audio_bytes = audio_file.read()
            if len(audio_bytes) > 1000:
                content_parts.append({"mime_type": "audio/wav", "data": audio_bytes})
        
        if extra_files:
            prompt += "\n【追加資料】\n(追加アップロードされた画像・音声)"
            for file in extra_files:
                file.seek(0)
                file_bytes = file.read()
                content_parts.append({"mime_type": file.type, "data": file_bytes})

        if current_details:
             prompt += f"\n【現在の分析データ】\n{json.dumps(current_details, ensure_ascii=False)}\nこれをもとに、新しい情報で更新してください。"

        prompt += """
        以下のJSON形式で結果を出力してください。数値は推測で構いませんが、厳しめに見積もってください。
        
        {
          "price_listing": "売出価格（整数、単位：万円。不明なら0）",
          "renovation_estimate": "音声内容に基づく概算リノベ費用（整数、単位：万円。水回り交換なら+200万など厳しめに）",
          "total_investment": "物件価格 + リノベ費用（整数、単位：万円）",
          "expected_revenue_monthly": "エリアと物件スペックからの想定月商（整数、単位：万円）",
          "roi_estimate": "表面利回り（％、小数第1位まで。年商÷総投資額）",
          "legal_risks": "再建築不可、消防法適合の難易度、民泊新法/旅館業法の許可ハードルなど（文字列）",
          "grade": "総合判定(S/A/B/C)",
          "bitter_advice": "辛口アドバイス（文字列）",
          "pros": "買うべき理由（文字列）",
          "cons": "懸念点（文字列）",
          "features_summary": "物件の特徴要約（文字列）"
        }
        """
        
        content_parts.append(prompt) 
        
        # Retry logic
        max_retries = 3
        last_error = None
        
        for attempt in range(max_retries):
            try:
                response = model.generate_content(
                    content_parts,
                    generation_config={"response_mime_type": "application/json"}
                )
                text = response.text.replace("```json", "").replace("```", "").strip()
                return json.loads(text)
            except Exception as e:
                last_error = e
                time.sleep(1)
        
        raise last_error

    except Exception as e:
        return {"error": str(e)}


# --- Session State Initialization ---
if "address_val" not in st.session_state: st.session_state.address_val = ""
if "map_center" not in st.session_state: st.session_state.map_center = [35.62, 135.06]
if "analysis_result" not in st.session_state: st.session_state.analysis_result = None
if "last_audio_id" not in st.session_state: st.session_state.last_audio_id = None
# UI State
if "view_mode" not in st.session_state: st.session_state.view_mode = "list"
if "selected_property_id" not in st.session_state: st.session_state.selected_property_id = None
if "last_geocoded_address" not in st.session_state: st.session_state.last_geocoded_address = ""

# --- Sidebar ---
with st.sidebar:
    st.header("設定")
    # Cloud Secret API Key check
    default_api_key = ""
    if "GEMINI_API_KEY" in st.secrets:
        default_api_key = st.secrets["GEMINI_API_KEY"]
    
    api_key = st.text_input("API Key (OpenAI / Gemini)", value=default_api_key, type="password", help="音声分析にはGemini APIキーが必要です")
    
    st.markdown("---")
    st.markdown("### Google Drive連携")
    if DRIVE_ENABLED:
        if os.path.exists('credentials.json'):
            st.success("✅ 設定ファイル検出済み")
        else:
            st.warning("⚠️ credentials.json が見つかりません")
    else:
        st.error("⚠️ 必要なライブラリがインストールされていません")
    
    st.markdown("---")
    st.info("Kyotango Property Platform v3.1")
    
    # Logout Button (Always show if credentials exist)
    if "credentials" in st.session_state and st.session_state.credentials:
        st.markdown("---")
        if st.button("🚪 ログアウト", type="secondary", use_container_width=True):
            if os.path.exists('token.json'):
                os.remove('token.json')
            st.session_state.credentials = None
            st.rerun()

# --- Login Logic ---
def check_login():
    if "credentials" not in st.session_state:
        st.session_state.credentials = None
    
    if st.session_state.credentials and st.session_state.credentials.valid:
        return True
    
    # Check token.json
    if os.path.exists('token.json'):
        try:
            creds = Credentials.from_authorized_user_file('token.json', SCOPES)
            if creds and creds.valid:
                st.session_state.credentials = creds
                return True
            elif creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
                st.session_state.credentials = creds
                with open('token.json', 'w') as token:
                    token.write(creds.to_json())
                return True
        except Exception as e:
            st.error(f"Token error: {e}")
    
    return False

def login():
    st.title("Kyotango Property Platform")
    st.subheader("ログインが必要です")
    
    if not os.path.exists('credentials.json'):
        st.error("⚠️ credentials.json が見つかりません。Secrets設定を確認してください。")
        return

    if st.button("Googleアカウントでログイン", type="primary"):
        flow = InstalledAppFlow.from_client_secrets_file(
            'credentials.json', SCOPES,
            redirect_uri='http://localhost:8502'
        )
        try:
            creds = flow.run_local_server(port=0)
            st.session_state.credentials = creds
            with open('token.json', 'w') as token:
                token.write(creds.to_json())
            st.rerun()
        except Exception as e:
            st.error(f"Login failed: {e}")

# --- Main Execution ---
if not check_login():
    login()
    st.stop()

# --- Main UI (Authenticated) ---
st.title("Kyotango Property Platform")
st.caption(f"Logged in as: {st.session_state.credentials.client_id[:10]}...")

# Tabs
tab_scout, tab_manage, tab_chat = st.tabs(["🔍 目利き(Scout)", "📂 物件台帳(Manage)", "💬 経営会議(Consultant)"])

# --- Scout Tab ---
with tab_scout:
    st.subheader("Step 1: 住所・エリア入力")
    
    col_addr_1, col_addr_2 = st.columns([3, 1])
    with col_addr_1:
        address_input = st.text_input("住所を入力してください（例：京丹後市網野町...）", value=st.session_state.address_val)
    with col_addr_2:
        if st.button("地図から取得"):
            st.info("下の地図をクリックして住所を取得できます")
    
    if address_input:
        st.session_state.address_val = address_input
        
        # Auto-Geocode (Only if address changed)
        if address_input != st.session_state.last_geocoded_address:
            coords = get_coords_from_address(address_input)
            if coords:
                lat, lon, precision = coords
                
                if precision == "exact":
                    st.success(f"📍 座標を取得しました: {lat:.5f}, {lon:.5f}")
                    st.session_state.map_center = [lat, lon]
                elif precision == "town":
                    st.warning("⚠️ 番地が特定できなかったため、町名レベルのエリアを表示します。地図をタップして正確な位置を指定してください。")
                    st.session_state.map_center = [lat, lon]
                else: # city
                    st.error("⚠️ 住所が特定できませんでした。京丹後市役所周辺を表示します。地図をタップして位置を指定してください。")
                    st.session_state.map_center = [lat, lon]
                
                st.session_state.last_geocoded_address = address_input
            else:
                st.error("システムエラー: 座標取得ロジックが失敗しました。")
                st.session_state.last_geocoded_address = address_input 

        # Show map preview & Capture Click (Always show if address is present)
        st.markdown("##### 🗺️ 位置確認・修正")
        st.caption("地図をクリックすると、その位置にピンが移動し、座標が更新されます。")
        
        # Use session state map center
        current_lat = st.session_state.map_center[0]
        current_lon = st.session_state.map_center[1]
        
        m_preview = folium.Map(location=[current_lat, current_lon], zoom_start=18, tiles=None, height=300)
        
        # Add Layers
        folium.TileLayer('Esri.WorldImagery', name='衛星写真 (Satellite)', attr='Esri', show=True).add_to(m_preview)
        folium.TileLayer('CartoDB positron', name='戦略マップ (Strategic)', show=False).add_to(m_preview)
        folium.TileLayer('OpenStreetMap', name='標準マップ (Standard)', show=False).add_to(m_preview)
        
        folium.LayerControl().add_to(m_preview)
        
        # Always show marker at current center
        folium.Marker(
            [current_lat, current_lon], 
            popup="選択中の位置",
            icon=folium.Icon(color="red", icon="info-sign")
        ).add_to(m_preview)
        
        # Capture click
        map_data = st_folium(m_preview, width="100%", height=300, returned_objects=["last_clicked"])
        
        if map_data and map_data.get("last_clicked"):
            clicked_lat = map_data["last_clicked"]["lat"]
            clicked_lng = map_data["last_clicked"]["lng"]
            
            # Update if clicked different location
            if abs(clicked_lat - current_lat) > 0.00001 or abs(clicked_lng - current_lon) > 0.00001:
                st.session_state.map_center = [clicked_lat, clicked_lng]
                st.rerun()
        
        # Display Coordinates
        st.info(f"📍 現在選択中の座標: 緯度 {st.session_state.map_center[0]:.5f}, 経度 {st.session_state.map_center[1]:.5f}")


        st.markdown("---")
        st.subheader("Step 2: 音声で内見メモ")
        st.info(f"📍 {address_input} の内見を開始します。気づきを録音してください。")

        col1, col2 = st.columns(2)
        with col1:
            audio_input = st.audio_input("マイクで録音")
        with col2:
            audio_upload = st.file_uploader("音声ファイルをアップロード", type=["mp3", "wav", "m4a"])
        
        # Image Upload
        st.markdown("##### 📸 現場写真・図面を追加")
        image_uploads = st.file_uploader(
            "気になった箇所（水回り、屋根、眺望など）の写真を何枚でもアップロードしてください", 
            type=['png', 'jpg', 'jpeg'], 
            accept_multiple_files=True
        )

        audio_source = audio_input if audio_input else audio_upload

        # Auto-Analysis Logic
        if audio_source:
            current_audio_id = f"{audio_source.name}-{audio_source.size}" if hasattr(audio_source, 'name') else str(audio_source.size)
            
            if st.session_state.last_audio_id != current_audio_id:
                if not api_key:
                    st.warning("分析を開始するにはAPIキーをサイドバーに入力してください。")
                else:
                    with st.spinner("Gemini 1.5 Flash が投資価値を分析中..."):
                        result = analyze_investment_value(api_key, st.session_state.address_val, audio_file=audio_source)
                        
                        if "error" in result:
                            st.error(f"解析エラー: {result['error']}")
                        else:
                            st.session_state.analysis_result = result
                            st.session_state.last_audio_id = current_audio_id
                            
                            # Update map center if possible
                            coords = get_coords_from_address(st.session_state.address_val)
                            if coords:
                                st.session_state.map_center = [coords[0], coords[1]]
                            
                            # Drive Backup (Scout Phase)
                            if DRIVE_ENABLED and os.path.exists('credentials.json'):
                                with st.spinner("Google Driveへバックアップ中..."):
                                    audio_source.seek(0)
                                    res = upload_file_to_drive(audio_source, f"scout_audio_{int(time.time())}.wav", st.session_state.address_val)
                                    st.toast(f"Drive: {res}")

    # --- Results Section ---
    if st.session_state.analysis_result:
        res = st.session_state.analysis_result
        
        st.markdown("---")
        st.subheader("📊 投資分析レポート")
        
        # Top Row: Grade and Key Metrics
        c1, c2, c3, c4 = st.columns(4)
        with c1:
            st.markdown("#### 総合判定")
            grade_class = "rating-b"
            if res.get('grade') == 'S': grade_class = "rating-s"
            elif res.get('grade') == 'A': grade_class = "rating-a"
            elif res.get('grade') == 'C': grade_class = "rating-c"
            st.markdown(f"<div class='{grade_class}'>{res.get('grade', '-')}</div>", unsafe_allow_html=True)
        with c2:
            st.metric("表面利回り (ROI)", f"{res.get('roi_estimate', 0)}%")
        with c3:
            st.metric("総投資額 (概算)", f"{res.get('total_investment', 0)}万円")
        with c4:
            st.metric("想定月商", f"{res.get('expected_revenue_monthly', 0)}万円")

        # Details
        with st.expander("詳細分析データ", expanded=True):
            col_d1, col_d2 = st.columns(2)
            with col_d1:
                st.markdown("##### 💰 財務分析")
                st.write(f"**売出価格**: {res.get('price_listing', 0)}万円")
                st.write(f"**リノベ費用**: {res.get('renovation_estimate', 0)}万円")
                st.write(f"**法的リスク**: {res.get('legal_risks', '特になし')}")
            with col_d2:
                st.markdown("##### 📝 物件特徴")
                st.write(res.get('features_summary', ''))
                st.markdown(f"**👍 Pros**: {res.get('pros', '')}")
                st.markdown(f"**👎 Cons**: {res.get('cons', '')}")

        # Bitter Advice
        st.markdown(f"""
        <div class="result-box">
            <h3>⚡️ 辛口アドバイス</h3>
            <p>{res.get('bitter_advice', '')}</p>
        </div>
        """, unsafe_allow_html=True)
        
        # Save Button
        if st.button("💾 この物件を台帳に保存", type="primary"):
            # Check if coordinates are default (Kyotango City Hall)
            lat = st.session_state.map_center[0]
            lon = st.session_state.map_center[1]
            
            # Default coords check (approximate)
            if abs(lat - 35.62) < 0.01 and abs(lon - 135.06) < 0.01:
                # Try to geocode again
                coords = get_coords_from_address(st.session_state.address_val)
                if coords:
                    lat, lon, _ = coords # Unpack 3 values
                else:
                    # If still fails, save as None so it doesn't show up at City Hall
                    lat, lon = None, None

            save_data = {
                "title": f"{datetime.now().strftime('%Y%m%d')}_{st.session_state.address_val}",
                "address": st.session_state.address_val,
                "latitude": lat,
                "longitude": lon,
                "price": res.get('price_listing', 0),
                "features": res.get('features_summary', ''),
                "rating": res.get('grade', '-'),
                "memo": res.get('bitter_advice', ''),
                "status": "検討中",
                "renovation_cost": res.get('renovation_estimate', 0),
                "roi": res.get('roi_estimate', 0.0),
                "details_json": json.dumps(res, ensure_ascii=False),
                "legal_risks": res.get('legal_risks', '')
            }
            new_prop_id = save_property(save_data)
            
            # Save Images
            if image_uploads and new_prop_id:
                img_dir = f"data/images/{new_prop_id}"
                os.makedirs(img_dir, exist_ok=True)
                
                for img_file in image_uploads:
                    with open(os.path.join(img_dir, img_file.name), "wb") as f:
                        f.write(img_file.getbuffer())
                
                # Drive Backup (Images)
                if DRIVE_ENABLED and os.path.exists('credentials.json'):
                    try:
                        drive_service = get_drive_service()
                        if drive_service:
                            pass
                    except: pass

            st.toast("物件を台帳に保存しました！", icon="✅")

# --- Manage Tab ---
with tab_manage:
    st.subheader("📂 物件台帳 (Portfolio)")
    
    df = get_all_properties()
    
    if df.empty:
        st.info("まだ保存された物件はありません。「目利き」タブから物件を保存してください。")
    else:
        # --- View A: List Mode ---
        if st.session_state.view_mode == "list":
            # Global Map
            st.markdown("#### 🗺️ 全体マップ (戦略ビュー)")
            
            # Filter for valid coordinates
            df['latitude'] = pd.to_numeric(df['latitude'], errors='coerce')
            df['longitude'] = pd.to_numeric(df['longitude'], errors='coerce')

            valid_df = df[
                (df['latitude'].notna()) & (df['latitude'] != 0) &
                (df['longitude'].notna()) & (df['longitude'] != 0)
            ]
            
            # Calculate bounds for auto-zoom
            if not valid_df.empty:
                min_lat, max_lat = valid_df['latitude'].min(), valid_df['latitude'].max()
                min_lon, max_lon = valid_df['longitude'].min(), valid_df['longitude'].max()
                
                center_lat = (min_lat + max_lat) / 2
                center_lon = (min_lon + max_lon) / 2
                
                is_single_point = (max_lat - min_lat < 0.001) and (max_lon - min_lon < 0.001)
            else:
                center_lat, center_lon = 35.62, 135.06 # Default Kyotango
                is_single_point = False

            m_portfolio = folium.Map(
                location=[center_lat, center_lon], 
                zoom_start=10 if not is_single_point else 14, 
                tiles=None, 
                height=400
            )
            
            # Add Layers
            folium.TileLayer('Esri.WorldImagery', name='衛星写真 (Satellite)', attr='Esri', show=True).add_to(m_portfolio)
            folium.TileLayer('CartoDB positron', name='戦略マップ (Strategic)', show=False).add_to(m_portfolio)
            folium.TileLayer('OpenStreetMap', name='標準マップ (Standard)', show=False).add_to(m_portfolio)
            
            folium.LayerControl().add_to(m_portfolio)
            
            # ------------------------------------------------------------------
            # [FIXED] Marker Logic: NO MarkerCluster, Correct Indentation
            # ------------------------------------------------------------------
            
            all_coords = []
            
            for index, row in valid_df.iterrows():
                # Color & Icon Logic
                status = row['status']
                if status == "購入済み":
                    color = "red"
                    icon_name = "home"
                elif status == "検討中":
                    color = "blue"
                    icon_name = "info-sign"
                elif status == "見送り":
                    color = "black"
                    icon_name = "remove"
                elif status == "未内見":
                    color = "gray"
                    icon_name = "question"
                else:
                    color = "orange"
                    icon_name = "star"
                
                # Directly add to map (m_portfolio)
                folium.Marker(
                    [row['latitude'], row['longitude']],
                    popup=f"<b>{row['title']}</b><br>価格: {row['price']}万円<br>利回り: {row['roi']}%",
                    tooltip=f"{row['title']} ({status})",
                    icon=folium.Icon(color=color, icon=icon_name)
                ).add_to(m_portfolio)
                
                all_coords.append([row['latitude'], row['longitude']])
            
            # Auto-Fit Bounds
            if all_coords:
                m_portfolio.fit_bounds(all_coords)

            # ------------------------------------------------------------------
            
            # Render Map & Capture Click
            map_data = st_folium(m_portfolio, width="100%", height=400, returned_objects=["last_object_clicked"])

            # Debug Info
            with st.expander("🛠️ マップデバッグ情報"):
                st.write(f"Valid Properties: {len(valid_df)}")
                if not valid_df.empty:
                    st.write(f"Bounds: [{min_lat}, {min_lon}] - [{max_lat}, {max_lon}]")

            # Handle Map Click
            if map_data and map_data.get("last_object_clicked"):
                clicked_lat = map_data["last_object_clicked"]["lat"]
                clicked_lng = map_data["last_object_clicked"]["lng"]
                
                # Find closest property
                clicked_prop = valid_df[
                    (valid_df['latitude'].between(clicked_lat - 0.0001, clicked_lat + 0.0001)) & 
                    (valid_df['longitude'].between(clicked_lng - 0.0001, clicked_lng + 0.0001))
                ]
                
                if not clicked_prop.empty:
                    prop_id = clicked_prop.iloc[0]['id']
                    st.session_state.selected_property_id = int(prop_id)
                    st.toast(f"物件を選択しました: {clicked_prop.iloc[0]['title']}")
                    # Optional: Auto-redirect
                    # st.session_state.view_mode = "detail"
                    # st.rerun()
            
            st.markdown("---")
            st.markdown("#### 📋 物件一覧")
            
            # Calculate Total Price
            df['total_price'] = df['price'] + df['renovation_cost']
            
            display_cols = ["id", "status", "title", "price", "renovation_cost", "total_price", "roi", "rating", "address", "latitude", "longitude"]
            st.dataframe(
                df[display_cols],
                column_config={
                    "price": st.column_config.NumberColumn("価格(万)", format="%d万"),
                    "renovation_cost": st.column_config.NumberColumn("リノベ(万)", format="%d万"),
                    "total_price": st.column_config.NumberColumn("総額(万)", format="%d万"),
                    "roi": st.column_config.NumberColumn("利回り", format="%.1f%%"),
                    "latitude": st.column_config.NumberColumn("緯度", format="%.5f"),
                    "longitude": st.column_config.NumberColumn("経度", format="%.5f"),
                },
                hide_index=True,
                use_container_width=True
            )
            
            # Navigation Control
            col_sel, col_btn = st.columns([3, 1])
            with col_sel:
                options = {f"{row['id']}: {row['title']} ({row['status']})": row['id'] for index, row in df.iterrows()}
                current_index = 0
                if st.session_state.selected_property_id:
                    for i, (k, v) in enumerate(options.items()):
                        if v == st.session_state.selected_property_id:
                            current_index = i
                            break
                
                selected_option_key = st.selectbox(
                    "詳細を見る物件を選択してください", 
                    list(options.keys()), 
                    index=current_index,
                    key="property_selector_list"
                )
            
            with col_btn:
                st.write("") # Spacer
                st.write("")
                if st.button("詳細へ移動 ➡️", type="primary"):
                    if selected_option_key:
                        st.session_state.selected_property_id = options[selected_option_key]
                        st.session_state.view_mode = "detail"
                        st.rerun()

            # --- Bulk Delete ---
            st.markdown("---")
            with st.expander("🗑️ 一括削除 (Bulk Delete)"):
                st.warning("選択した物件を完全に削除します。この操作は取り消せません。")
                
                delete_options = {f"{row['id']}: {row['title']}": row['id'] for index, row in df.iterrows()}
                selected_delete_keys = st.multiselect(
                    "削除する物件を選択してください",
                    list(delete_options.keys())
                )
                
                if st.button("選択した物件を削除する", type="primary", key="bulk_delete_btn"):
                    if selected_delete_keys:
                        deleted_count = 0
                        for key in selected_delete_keys:
                            prop_id = delete_options[key]
                            delete_property(prop_id)
                            deleted_count += 1
                        
                        st.toast(f"{deleted_count}件の物件を削除しました")
                        time.sleep(1)
                        st.rerun()
                    else:
                        st.warning("削除する物件が選択されていません")

        # --- View B: Detail Mode ---
        elif st.session_state.view_mode == "detail":
            if st.session_state.selected_property_id is None:
                st.session_state.view_mode = "list"
                st.rerun()
            
            selected_row = df[df['id'] == st.session_state.selected_property_id].iloc[0]
            
            # Back Button
            if st.button("⬅️ 物件一覧に戻る"):
                st.session_state.view_mode = "list"
                st.rerun()
            
            st.markdown("---")
            
            # Dashboard Content
            st.markdown(f"### {selected_row['title']}")
            
            # Status Control
            col_status, col_metrics = st.columns([1, 2])
            with col_status:
                new_status = st.selectbox(
                    "現在のステータス",
                    ["検討中", "購入済み", "見送り", "未内見"],
                    index=["検討中", "購入済み", "見送り", "未内見"].index(selected_row['status']) if selected_row['status'] in ["検討中", "購入済み", "見送り", "未内見"] else 0,
                    key="status_selector_detail"
                )
                
                if st.button("💾 変更を保存", type="primary", key="save_status_btn"):
                    update_property(selected_row['id'], "status", new_status)
                    st.toast("ステータスを更新しました！")
                    time.sleep(0.5)
                    st.rerun()
            
            with col_metrics:
                m1, m2, m3 = st.columns(3)
                with m1: st.metric("物件価格", f"{selected_row['price']}万円")
                with m2: st.metric("リノベ概算", f"{selected_row['renovation_cost']}万円")
                with m3: st.metric("表面利回り", f"{selected_row['roi']}%")

            # Map & Location Fix
            with st.expander("📍 地図・位置情報修正", expanded=True):
                lat = selected_row['latitude']
                lon = selected_row['longitude']
                
                if pd.isna(lat) or pd.isna(lon) or lat == 0 or lon == 0:
                    st.warning("⚠️ 座標が設定されていません。手動で入力するか、住所から再取得してください。")
                    map_lat, map_lon = 35.62, 135.06
                    has_valid_coords = False
                else:
                    map_lat, map_lon = lat, lon
                    has_valid_coords = True

                st.markdown("##### 座標の手動修正")
                st.info("🗺 地図をタップして、ピンを正しい建物の真上に移動させてください")

                if "fix_lat" not in st.session_state or st.session_state.get("fix_prop_id") != selected_row['id']:
                    st.session_state.fix_lat = selected_row['latitude'] if pd.notna(selected_row['latitude']) else 0.0
                    st.session_state.fix_lon = selected_row['longitude'] if pd.notna(selected_row['longitude']) else 0.0
                    st.session_state.fix_prop_id = selected_row['id']

                display_lat = st.session_state.fix_lat if st.session_state.fix_lat != 0 else map_lat
                display_lon = st.session_state.fix_lon if st.session_state.fix_lon != 0 else map_lon

                m_detail = folium.Map(
                    location=[display_lat, display_lon], 
                    zoom_start=18,
                    tiles='Esri.WorldImagery',
                    attr='Esri',
                    height=400
                )
                
                if has_valid_coords:
                    folium.Marker(
                        [display_lat, display_lon],
                        popup=selected_row['title'],
                        icon=folium.Icon(color="red" if selected_row['status'] == "購入済み" else "blue")
                    ).add_to(m_detail)
                
                map_data = st_folium(m_detail, width="100%", height=400, returned_objects=["last_clicked"])
                
                if map_data and map_data.get("last_clicked"):
                    clicked_lat = map_data["last_clicked"]["lat"]
                    clicked_lng = map_data["last_clicked"]["lng"]
                    
                    if abs(clicked_lat - st.session_state.fix_lat) > 0.000001 or abs(clicked_lng - st.session_state.fix_lon) > 0.000001:
                        st.session_state.fix_lat = clicked_lat
                        st.session_state.fix_lon = clicked_lng
                        st.rerun()

                c_lat, c_lon, c_btn = st.columns([2, 2, 1])
                with c_lat:
                    new_lat = st.number_input("Latitude", value=st.session_state.fix_lat, format="%.6f", key="input_fix_lat")
                with c_lon:
                    new_lon = st.number_input("Longitude", value=st.session_state.fix_lon, format="%.6f", key="input_fix_lon")
                with c_btn:
                    st.write("") # Spacer
                    st.write("")
                    if st.button("座標更新"):
                        update_property(selected_row['id'], "latitude", new_lat)
                        update_property(selected_row['id'], "longitude", new_lon)
                        st.toast("座標を更新しました！")
                        time.sleep(0.5)
                        st.rerun()

                if st.button("住所から座標を再取得 (京都府付与)"):
                    coords = get_coords_from_address(selected_row['address'])
                    if coords:
                        new_lat, new_lon, precision = coords
                        update_property(selected_row['id'], "latitude", new_lat)
                        update_property(selected_row['id'], "longitude", new_lon)
                        
                        msg = "座標を更新しました！"
                        if precision != "exact":
                            msg += f" (精度: {precision} - 地図で微調整してください)"
                        
                        st.toast(msg)
                        time.sleep(1)
                        st.rerun()
                    else:
                        st.error("座標を取得できませんでした。")
                
                # Photo Gallery
                st.markdown("---")
                st.subheader("🖼 物件アルバム")
                
                img_dir = f"data/images/{selected_row['id']}"
                if os.path.exists(img_dir):
                    images = [f for f in os.listdir(img_dir) if f.lower().endswith(('.png', '.jpg', '.jpeg'))]
                    if images:
                        cols = st.columns(3)
                        for idx, img_file in enumerate(images):
                            with cols[idx % 3]:
                                st.image(os.path.join(img_dir, img_file), use_container_width=True, caption=img_file)
                    else:
                        st.info("写真はまだありません。")
                else:
                    st.info("写真はまだありません。")

                st.markdown("##### ➕ 写真を追加")
                new_photos = st.file_uploader("追加の写真を選択", type=['png', 'jpg', 'jpeg'], accept_multiple_files=True, key="add_photos_manage")
                if new_photos:
                    os.makedirs(img_dir, exist_ok=True)
                    for img_file in new_photos:
                        with open(os.path.join(img_dir, img_file.name), "wb") as f:
                            f.write(img_file.getbuffer())
                    st.toast("写真を追加しました！")
                    time.sleep(1)
                    st.rerun()
            
            # --- Enhanced: Evidence Upload & Re-Analysis ---
            st.markdown("---")
            st.markdown("#### 📸 追加資料・再鑑定")
            
            uploaded_files = st.file_uploader("写真や音声を追加して再鑑定 (Driveへ自動保存)", accept_multiple_files=True, key="detail_uploader")
            
            if uploaded_files:
                with st.spinner("Google Driveへバックアップ中..."):
                    for f in uploaded_files:
                        f.seek(0)
                        res = upload_file_to_drive(f, f.name, selected_row['address'])
                    st.toast("バックアップ完了！")
                
                if st.button("🔄 追加資料を含めて再鑑定"):
                    if not api_key:
                        st.error("APIキーが必要です。")
                    else:
                        with st.spinner("Gemini 1.5 Flash が再分析中..."):
                            current_details = {}
                            try: current_details = json.loads(selected_row['details_json'])
                            except: pass
                            
                            new_result = analyze_investment_value(
                                api_key, 
                                selected_row['address'], 
                                extra_files=uploaded_files, 
                                current_details=current_details
                            )
                            
                            if "error" in new_result:
                                st.error(f"再解析エラー: {new_result['error']}")
                            else:
                                update_property(selected_row['id'], "price", new_result.get('price_listing', 0))
                                update_property(selected_row['id'], "renovation_cost", new_result.get('renovation_estimate', 0))
                                update_property(selected_row['id'], "roi", new_result.get('roi_estimate', 0.0))
                                update_property(selected_row['id'], "rating", new_result.get('grade', '-'))
                                update_property(selected_row['id'], "details_json", json.dumps(new_result, ensure_ascii=False))
                                update_property(selected_row['id'], "legal_risks", new_result.get('legal_risks', ''))
                                
                                st.success("再鑑定が完了しました！データが更新されました。")
                                time.sleep(1)
                                st.rerun()

            # Analysis & Memo
            st.markdown("#### 📝 分析・メモ")
            
            details = {}
            try: details = json.loads(selected_row['details_json'])
            except: pass
            
            st.info(f"💡 **辛口アドバイス**: {selected_row['memo']}")
            if 'legal_risks' in selected_row and selected_row['legal_risks']:
                 st.warning(f"⚠️ **法的リスク**: {selected_row['legal_risks']}")
            
            st.markdown("##### 追記メモ")
            user_memo = st.text_area("自由にメモを残せます", value=selected_row['memo'], height=100, key="user_memo_area_detail")
            if st.button("メモを保存"):
                update_property(selected_row['id'], "memo", user_memo)
                st.toast("メモを保存しました！")

            st.markdown("---")
            st.markdown("##### 🗑️ 物件の削除")
            with st.expander("削除メニューを開く"):
                st.warning("この操作は取り消せません。本当に削除しますか？")
                if st.button("物件を完全に削除する", type="primary"):
                    delete_property(selected_row['id'])
                    st.toast("物件を削除しました")
                    st.session_state.selected_property_id = None
                    st.session_state.view_mode = "list"
                    time.sleep(1)
                    st.rerun()

# --- Consultant Tab ---
with tab_chat:
    st.subheader("💬 経営会議 (Consultant)")
    st.info("あなたの物件ポートフォリオに基づき、AIコンサルタントがアドバイスします。")
    
    if "messages" not in st.session_state:
        st.session_state.messages = []

    for message in st.session_state.messages:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])

    if prompt := st.chat_input("相談したいことを入力してください..."):
        st.session_state.messages.append({"role": "user", "content": prompt})
        with st.chat_message("user"):
            st.markdown(prompt)

        with st.chat_message("assistant"):
            if not api_key:
                st.error("APIキーを設定してください。")
            else:
                with st.spinner("コンサルタントが思考中..."):
                    properties_df = get_all_properties()
                    portfolio_summary = ""
                    if not properties_df.empty:
                        for _, row in properties_df.iterrows():
                            portfolio_summary += f"- 【{row['status']}】{row['address']} (価格:{row['price']}万, 利回り:{row['roi']}%, リスク:{row.get('legal_risks', 'なし')})\n"
                    else:
                        portfolio_summary = "物件データなし"
                    
                    system_prompt = f"""
                    あなたは京丹後で民泊事業を拡大する女性オーナーの専属コンサルタントです。
                    
                    【ユーザーの現在の状況】
                    - 掃除担当：Aさん（網野エリア担当）、Bさん（丹後町エリア担当）
                    - 理念：数を追うより、地域の文化を守れる古民家を再生したい。
                    - 課題：これ以上エリアを広げると管理が回らなくなる恐れがある。
                    
                    【現在の物件ポートフォリオ】
                    {portfolio_summary}
                    
                    上記の情報を踏まえ、ユーザーの質問に対して具体的かつ論理的にアドバイスしてください。
                    特に、エリアごとの掃除担当の負荷や、ポートフォリオ全体のバランス（高利回り物件と文化財物件の比率など）を考慮してください。
                    """
                    
                    try:
                        genai.configure(api_key=api_key)
                        model = genai.GenerativeModel("gemini-flash-latest")
                        
                        chat = model.start_chat(history=[])
                        response = chat.send_message(system_prompt + "\n\nユーザーの質問: " + prompt)
                        
                        st.markdown(response.text)
                        st.session_state.messages.append({"role": "assistant", "content": response.text})
                    except Exception as e:
                        st.error(f"エラーが発生しました: {e}")
