# app.py
import os
import sqlite3
import uuid
import requests
from io import BytesIO
from datetime import datetime, timedelta
from contextlib import closing
import streamlit as st
import streamlit.components.v1 as components
import pandas as pd
from PIL import Image
from streamlit_js_eval import get_geolocation
import config
import database as db

# Try to import Google Vision if enabled
vision = None
if config.USE_VISION:
    try:
        from google.cloud import vision as gcv
        vision = gcv
    except Exception as e:
        st.warning(f"Could not import Google Vision: {e}")
        config.USE_VISION = False

# --- General Helper Functions ---
def serial_generate(): return f"WO-{uuid.uuid4().hex[:8].upper()}"
def within_edit_window(editable_until_iso):
    try: return datetime.utcnow() <= datetime.fromisoformat(editable_until_iso)
    except Exception: return False
def img_to_bytes(img: Image.Image):
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()
def valid_upload(uploaded_file):
    if uploaded_file is None: return True, "No file"
    size_mb = len(uploaded_file.getvalue()) / (1024 * 1024)
    ext = uploaded_file.name.split(".")[-1].lower()
    if size_mb > config.MAX_FILE_SIZE_MB: return False, f"File too large ({size_mb:.2f}MB). Max {config.MAX_FILE_SIZE_MB}MB."
    if ext not in config.ALLOWED_TYPES: return False, f"Allowed types: {', '.join(config.ALLOWED_TYPES)}."
    return True, "OK"
def run_vision_labels(image_bytes: bytes):
    if not config.USE_VISION or vision is None: return "Vision disabled or not available."
    try:
        client = vision.ImageAnnotatorClient()
        image = vision.Image(content=image_bytes)
        response = client.label_detection(image=image)
        labels = response.label_annotations
        return ", ".join([f"{l.description}({l.score:.2f})" for l in labels[:10]]) if labels else "No labels detected."
    except Exception as e: return f"Vision error: {e}"
def reverse_geocode_google(lat, lon):
    if not config.GOOGLE_MAPS_API_KEY: return None
    try:
        response = requests.get("https://maps.googleapis.com/maps/api/geocode/json", params={"latlng": f"{lat},{lon}", "key": config.GOOGLE_MAPS_API_KEY, "language": "ar"})
        response.raise_for_status()
        results = response.json().get('results', [])
        if not results: return None
        address_components = results[0].get('address_components', [])
        address = {}
        for component in address_components:
            types = component.get('types', [])
            if 'administrative_area_level_1' in types: address['governorate'] = component['long_name'].replace('محافظة ', '')
            if 'administrative_area_level_2' in types: address['district'] = component['long_name']
            if 'locality' in types: address['city'] = component['long_name']
            if 'sublocality_level_1' in types and 'city' not in address: address['city'] = component['long_name']
        return address
    except requests.exceptions.RequestException as e:
        st.error(f"Geocoding API request failed: {e}")
        return None

# --- UI Helper Functions ---
def logout_button():
    if st.sidebar.button("Logout"):
        st.session_state.clear()
        st.rerun()
def Google_Maps_iframe(lat, lon, zoom=15, width=800, height=400):
    if not config.GOOGLE_MAPS_API_KEY:
        st.info("Set GOOGLE_MAPS_API_KEY in .env to render Google Maps iframe.")
        return
    url = f"https://www.google.com/maps/embed/v1/place?key={config.GOOGLE_MAPS_API_KEY}&q={lat},{lon}&zoom={zoom}"
    components.iframe(url, width=width, height=height)
def get_districts_for_governorate(governorate):
    if governorate in config.EGYPT_LOCATIONS: return list(config.EGYPT_LOCATIONS[governorate]["districts"].keys())
    return []
def get_cities_for_district(governorate, district):
    if governorate in config.EGYPT_LOCATIONS and district in config.EGYPT_LOCATIONS[governorate]["districts"]:
        return config.EGYPT_LOCATIONS[governorate]["districts"][district]
    return []
def geolocation_map_selector(height=400):
    if not config.GOOGLE_MAPS_API_KEY:
        st.warning("Google Maps API key is required for map functionality.")
        return st.number_input("Latitude", format="%.6f"), st.number_input("Longitude", format="%.6f")
    
    if 'current_lat' not in st.session_state: st.session_state.current_lat = config.DEFAULT_LAT
    if 'current_lon' not in st.session_state: st.session_state.current_lon = config.DEFAULT_LON
    
    try:
        geo = get_geolocation()
        if geo and 'coords' in geo:
            st.session_state.current_lat = float(geo['coords']['latitude'])
            st.session_state.current_lon = float(geo['coords']['longitude'])
    except Exception: pass
    
    map_html = f"""
    <!DOCTYPE html><html><head>
    <script async defer src="https://maps.googleapis.com/maps/api/js?key={config.GOOGLE_MAPS_API_KEY}&callback=initMap"></script>
    <style>#map{{height:{height}px;width:100%;border-radius:8px;}}</style></head><body><div id="map"></div>
    <script>
    let map, marker;
    function initMap(){{
        const initialPos = {{lat:{st.session_state.current_lat}, lng:{st.session_state.current_lon}}};
        map = new google.maps.Map(document.getElementById("map"),{{zoom:15,center:initialPos,mapTypeId:'roadmap'}});
        marker = new google.maps.Marker({{position:initialPos,map:map,draggable:true}});
        map.addListener("click",(e) => {{marker.setPosition(e.latLng); updateCoords(e.latLng);}});
        marker.addListener("dragend",(e) => updateCoords(e.latLng));
    }}
    function updateCoords(latLng){{
        const lat=latLng.lat(), lng=latLng.lng();
        window.parent.postMessage({{type:'streamlit:setComponentValue',value:{{lat:lat,lng:lng}}}},'*');
    }}
    </script></body></html>
    """
    map_location = components.html(map_html, height=height + 10)
    if isinstance(map_location, dict):
        if 'lat' in map_location:
            st.session_state.current_lat = map_location['lat']
        if 'lng' in map_location:
            st.session_state.current_lon = map_location['lng']

    with st.expander("🔧 Or enter coordinates manually"):
        col1, col2 = st.columns(2)
        manual_lat = col1.number_input("Latitude", value=st.session_state.current_lat, format="%.6f")
        manual_lon = col2.number_input("Longitude", value=st.session_state.current_lon, format="%.6f")
        if manual_lat != st.session_state.current_lat or manual_lon != st.session_state.current_lon:
            st.session_state.current_lat, st.session_state.current_lon = manual_lat, manual_lon
            st.rerun()

    return st.session_state.current_lat, st.session_state.current_lon

# --- Main Page Views ---
def login_view():
    st.title(f"🔐 {config.APP_TITLE}")
    st.markdown("---")
    _, col, _ = st.columns([1, 2, 1])
    with col:
        with st.container(border=True):
            st.subheader("Login")
            defaults = list(config.USERS.keys())
            default_user = defaults[0] if defaults else ""
            username = st.text_input("Username", value=default_user, key="login_user")
            password = st.text_input("Password", type="password", value=config.USERS.get(default_user, {}).get("password",""), key="login_pass")
            if st.button("Login", use_container_width=True, type="primary"):
                user_info = config.USERS.get(username)
                if user_info and password == user_info["password"]:
                    st.session_state["auth_user"] = {"username": username, "role": user_info["role"]}
                    st.success(f"Welcome {username}!")
                    st.rerun()
                else: st.error("Invalid username or password.")

def technician_view():
    st.header(f"🛠️ Technician Portal")
    if 'custom_governorates' not in st.session_state: st.session_state.custom_governorates = []
    if 'custom_regions' not in st.session_state: st.session_state.custom_regions = {}
    if 'geocoded_address' not in st.session_state: st.session_state.geocoded_address = {}
    if 'last_geocoded_lat' not in st.session_state: st.session_state.last_geocoded_lat = None

    tab1, tab2 = st.tabs(["📝 Create New Work Order", "🗂️ My Work Orders"])

    with tab1:
        st.subheader("Create a New Work Order")
        lat, lon = geolocation_map_selector()
        if lat != st.session_state.get('last_geocoded_lat') or lon != st.session_state.get('last_geocoded_lon'):
            with st.spinner("🔄 Automatically detecting address..."):
                geocoded = reverse_geocode_google(lat, lon)
                if geocoded:
                    st.session_state.geocoded_address = geocoded
                    st.session_state.last_geocoded_lat, st.session_state.last_geocoded_lon = lat, lon
                    st.rerun()
        auto_address = st.session_state.geocoded_address

        with st.form("create_wo_form"):
            st.markdown("##### 1. Facility Details")
            f_type = st.selectbox("Facility Type", config.FACILITY_TYPES)
            f_desc = st.selectbox("Facility Description", config.FACILITY_DESCRIPTIONS)
            st.divider()

            st.markdown(f"##### 2. Location (Coordinates: `{lat:.5f}, {lon:.5f}`)")
            all_govs = list(config.EGYPT_LOCATIONS.keys()) + st.session_state.custom_governorates
            try: gov_idx = all_govs.index(auto_address.get('governorate'))
            except (ValueError, AttributeError): gov_idx = 0
            gov = st.selectbox("Governorate", all_govs, index=gov_idx)
            
            districts = get_districts_for_governorate(gov)
            try: dist_idx = districts.index(auto_address.get('district'))
            except (ValueError, AttributeError): dist_idx = 0
            dist = st.selectbox("District/City", districts, index=dist_idx)
            
            cities = get_cities_for_district(gov, dist)
            try: city_idx = cities.index(auto_address.get('city'))
            except (ValueError, AttributeError): city_idx = 0
            city = st.text_input("Village/Neighborhood", value=auto_address.get('city',''))
            st.divider()

            st.markdown("##### 3. Maintenance Photos")
            c1,c2,c3=st.columns(3)
            ext_img = c1.file_uploader("Facility Photo", type=config.ALLOWED_TYPES)
            before_img = c2.file_uploader("Before", type=config.ALLOWED_TYPES)
            after_img = c3.file_uploader("After", type=config.ALLOWED_TYPES)
            st.divider()

            st.markdown("##### 4. Finalize")
            maint_type = st.selectbox("Maintenance Type", config.MAINT_TYPES)
            submitted = st.form_submit_button("✅ Save Work Order", use_container_width=True, type="primary")
            if submitted:
                with closing(sqlite3.connect(config.DB_PATH)) as conn:
                    # Validation and data processing logic...
                    facility_id = db.store_facility(conn, {"type":f_type, "description":f_desc, "governorate":gov, "district":dist, "city":city, "lat":lat, "lon":lon, "external_image":img_to_bytes(Image.open(ext_img)) if ext_img else None, "vision_labels":""})
                    created = db.now_iso()
                    db.store_work_order(conn, {"serial":serial_generate(), "technician":st.session_state["auth_user"]["username"], "facility_id":facility_id, "maintenance_type":maint_type, "before_image":img_to_bytes(Image.open(before_img)) if before_img else None, "after_image":img_to_bytes(Image.open(after_img)) if after_img else None, "status":"Draft", "created_at":created, "last_saved_at":created, "editable_until":(datetime.utcnow()+timedelta(minutes=config.EDIT_WINDOW_MINUTES)).isoformat()})
                    st.success("Work order created!")
                    st.rerun()

    with tab2:
        st.subheader("My Work Order History")
        with closing(sqlite3.connect(config.DB_PATH)) as conn:
            df = db.fetch_work_orders(conn, "technician", st.session_state["auth_user"]["username"])
            if df.empty: st.info("No work orders found.")
            else:
                st.dataframe(df, use_container_width=True)
                st.divider()
                with st.expander("✏️ Edit or Request Changes"):
                    # ... Edit/Request logic from previous steps goes here ...
                    pass

def admin_view():
    st.header("👑 Admin Portal")
    tab1, tab2, tab3 = st.tabs(["📊 Dashboard", "🗂️ Work Order Explorer", "📬 Pending Edit Requests"])
    with closing(sqlite3.connect(config.DB_PATH)) as conn:
        with tab1:
            st.subheader("Key Performance Indicators")
            cur = conn.cursor()
            total_wo = cur.execute("SELECT COUNT(*) FROM work_orders").fetchone()[0]
            draft_wo = cur.execute("SELECT COUNT(*) FROM work_orders WHERE status='Draft'").fetchone()[0]
            locked_wo = cur.execute("SELECT COUNT(*) FROM work_orders WHERE status='Locked'").fetchone()[0]
            pending_edits = cur.execute("SELECT COUNT(*) FROM edit_requests WHERE status='Pending'").fetchone()[0]
            c1,c2,c3=st.columns(3)
            c1.metric("Total Work Orders", total_wo)
            c2.metric("Draft Orders", draft_wo)
            c3.metric("Locked Orders", locked_wo)
            st.metric("Pending Edit Requests", pending_edits, delta_color="inverse")
            st.divider()
            # ... Other dashboard charts and maps ...

        with tab2:
            st.subheader("View and Filter All Work Orders")
            df = db.fetch_work_orders(conn, "admin", "")
            if df.empty: st.info("No work orders found.")
            else:
                df_filtered = df.copy()
                for col in ['governorate', 'district', 'city_or_village']: df_filtered[col] = df_filtered[col].fillna('N/A')
                c1,c2,c3=st.columns(3)
                govs = ['All'] + sorted(df_filtered['governorate'].unique().tolist())
                sel_gov = c1.selectbox("Governorate", govs)
                if sel_gov != 'All': df_filtered = df_filtered[df_filtered['governorate'] == sel_gov]
                dists = ['All'] + sorted(df_filtered['district'].unique().tolist())
                sel_dist = c2.selectbox("District/City", dists)
                if sel_dist != 'All': df_filtered = df_filtered[df_filtered['district'] == sel_dist]
                cities = ['All'] + sorted(df_filtered['city_or_village'].unique().tolist())
                sel_city = c3.selectbox("Village/Neighborhood", cities)
                if sel_city != 'All': df_filtered = df_filtered[df_filtered['city_or_village'] == sel_city]
                st.dataframe(df_filtered, use_container_width=True)
                # ... Image viewer logic ...

        with tab3:
            st.subheader("Review Submitted Edit Requests")
            er = db.fetch_edit_requests(conn, status_filter="Pending")
            if er.empty: st.success("✅ No pending edit requests.")
            else:
                st.warning(f"You have {len(er)} request(s) to review.")
                st.dataframe(er, use_container_width=True)
                # ... Approval/rejection logic ...

# --- Main Application Runner ---
def main():
    st.set_page_config(page_title=config.APP_TITLE, layout="wide", initial_sidebar_state="expanded")
    db.init_db()
    if "auth_user" not in st.session_state:
        login_view()
        return
    user = st.session_state["auth_user"]
    st.sidebar.title(f"Welcome, {user['username']}")
    st.sidebar.info(f"Role: **{user['role'].capitalize()}**")
    logout_button()
    st.sidebar.markdown("---")
    
    if user["role"] == "technician": technician_view()
    elif user["role"] == "admin": admin_view()
    else: st.error("Unknown role")

if __name__ == "__main__":
    main()